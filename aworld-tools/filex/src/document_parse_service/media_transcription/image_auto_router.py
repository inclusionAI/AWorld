"""Automatic scene routing for image understanding."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from .backend import MediaTranscriptionBackend
from .models import TranscriptResult

logger = logging.getLogger(__name__)

_SCENE_TYPES = {
    "chart",
    "document",
    "general",
    "multi_object",
    "single_object",
}
_WHOLE_IMAGE_MODES = {"general", "single", "single_object", "whole", "whole_image"}
_UNKNOWN_VALUES = {"", "na", "n/a", "none", "null", "unknown", "无法辨认", "不可辨认"}
_PILL_SHAPES = {
    "capsule/oblong": "Capsule/Oblong",
    "capsule": "Capsule/Oblong",
    "oblong": "Capsule/Oblong",
    "oval": "Oval",
    "round": "Round",
    "square": "Square",
    "rectangle": "Rectangle",
    "diamond": "Diamond",
    "triangle": "Triangle",
    "five-sided": "Five-sided",
    "six-sided": "Six-sided",
    "eight-sided": "Eight-sided",
    "egg-shape": "Egg-shape",
    "u-shape": "U-shape",
}


@dataclass(frozen=True, slots=True)
class ImageObjectRegion:
    """One object region represented in normalized 0-1000 coordinates."""

    object_id: str
    bbox: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ImageSceneDecision:
    """Normalized scene classifier result."""

    scene_type: str
    confidence: float
    object_count_estimate: int
    objects: tuple[ImageObjectRegion, ...]


@dataclass(frozen=True, slots=True)
class ImageObjectCrop:
    """One persisted crop used as searchable visual evidence."""

    object_id: str
    bbox: tuple[int, int, int, int]
    local_path: Path | None
    crop_ref: str


@dataclass(frozen=True, slots=True)
class ImageContactSheet:
    """A VLM contact sheet and the object crops it contains."""

    path: Path
    objects: tuple[ImageObjectCrop, ...]


class ImageAutoRouterBackend:
    """Route images to whole-image or crop-first VLM parsing."""

    def __init__(self, backend: MediaTranscriptionBackend) -> None:
        self._backend = backend
        self.name = getattr(backend, "name", "image_vlm")

    async def transcribe(
        self,
        file_path: Path,
        *,
        media_type: str,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
    ) -> TranscriptResult:
        mode = self._resolve_mode(options)
        if mode in _WHOLE_IMAGE_MODES:
            result = await self._transcribe_whole_image(
                file_path=file_path,
                media_type=media_type,
                file_type=file_type,
                source_file_name=source_file_name,
                options=options,
            )
            result.metadata.update(
                {
                    "image_parse_mode": mode,
                    "image_scene_type": "single_object" if mode == "single_object" else "general",
                    "image_selected_pipeline": "whole_image_vlm",
                    "image_scene_detection_call_count": 0,
                }
            )
            return result

        decision, detection_result = await self._detect_scene(
            file_path=file_path,
            file_type=file_type,
            source_file_name=source_file_name,
            options=options,
            forced_multi_object=mode == "multi_object",
        )
        if decision.scene_type != "multi_object" or len(decision.objects) < 2:
            result = await self._transcribe_whole_image(
                file_path=file_path,
                media_type=media_type,
                file_type=file_type,
                source_file_name=source_file_name,
                options=options,
            )
            self._merge_routing_metadata(
                result,
                decision=decision,
                detection_result=detection_result,
                selected_pipeline="whole_image_vlm",
                mode=mode,
                fallback_reason=self._fallback_reason(decision, mode),
            )
            return result

        result = await self._transcribe_multi_object(
            file_path=file_path,
            file_type=file_type,
            source_file_name=source_file_name,
            options=options,
            decision=decision,
        )
        self._merge_routing_metadata(
            result,
            decision=decision,
            detection_result=detection_result,
            selected_pipeline="detect_crop_batch_vlm",
            mode=mode,
        )
        return result

    async def _detect_scene(
        self,
        *,
        file_path: Path,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
        forced_multi_object: bool,
    ) -> tuple[ImageSceneDecision, TranscriptResult]:
        detection_options = dict(options)
        detection_options["prompt"] = self._scene_detection_prompt(
            forced_multi_object=forced_multi_object,
        )
        detection_options["temperature"] = 0
        detection_options["max_tokens"] = int(
            options.get("image_scene_max_tokens") or 8192
        )
        detection_result = await self._backend.transcribe(
            file_path,
            media_type="image",
            file_type=file_type,
            source_file_name=source_file_name,
            options=detection_options,
        )
        decision = self._parse_scene_decision(
            detection_result.text,
            forced_multi_object=forced_multi_object,
            max_objects=self._positive_int(options.get("multi_object_max_objects"), 32),
        )
        return decision, detection_result

    async def _transcribe_multi_object(
        self,
        *,
        file_path: Path,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
        decision: ImageSceneDecision,
    ) -> TranscriptResult:
        user_intent = str(options.get("intent") or "").strip()
        target_fields = self._normalize_target_fields(options.get("target_fields"))
        original_prompt = str(options.get("prompt") or "").strip()
        extraction_profile = self._resolve_extraction_profile(
            options=options,
            user_intent=user_intent,
            target_fields=target_fields,
        )
        pill_profile = extraction_profile == "pill_search"
        batch_size = self._positive_int(
            options.get("multi_object_batch_size"),
            4 if pill_profile else 6,
        )
        concurrency = self._positive_int(
            options.get("multi_object_concurrency"),
            3 if pill_profile else 4,
        )
        padding_ratio = self._bounded_float(
            options.get("multi_object_padding_ratio"),
            default=0.08,
            minimum=0.0,
            maximum=0.3,
        )
        asset_output_dir = self._resolve_asset_output_dir(
            options.get("image_asset_output_dir")
        )

        with tempfile.TemporaryDirectory(prefix="filex-image-objects-") as temp_dir:
            contact_sheets = self._build_contact_sheets(
                file_path=file_path,
                regions=decision.objects,
                sheet_output_dir=Path(temp_dir),
                asset_output_dir=asset_output_dir,
                batch_size=batch_size,
                padding_ratio=padding_ratio,
            )
            semaphore = asyncio.Semaphore(concurrency)

            async def parse_batch(
                batch_index: int,
                contact_sheet: ImageContactSheet,
            ) -> TranscriptResult:
                async with semaphore:
                    batch_options = dict(options)
                    batch_options["prompt"] = self._multi_object_prompt(
                        user_intent=user_intent,
                        target_fields=target_fields,
                        original_prompt=original_prompt,
                        object_ids=tuple(
                            crop.object_id for crop in contact_sheet.objects
                        ),
                        extraction_profile=extraction_profile,
                    )
                    batch_options["temperature"] = 0
                    result = await self._backend.transcribe(
                        contact_sheet.path,
                        media_type="image",
                        file_type="png",
                        source_file_name=f"{source_file_name}-objects-{batch_index:03d}",
                        options=batch_options,
                    )
                    expected_ids = tuple(
                        crop.object_id for crop in contact_sheet.objects
                    )
                    if self._has_expected_objects(result.text, expected_ids):
                        return result
                    retry_options = dict(batch_options)
                    retry_options["prompt"] = (
                        f"{batch_options['prompt']}\n"
                        "上一次响应不是完整合法的 JSON。重新观察原图，只返回一个合法 JSON 对象；"
                        "不要使用代码围栏，必须覆盖全部对象编号。"
                    )
                    retry_result = await self._backend.transcribe(
                        contact_sheet.path,
                        media_type="image",
                        file_type="png",
                        source_file_name=(
                            f"{source_file_name}-objects-{batch_index:03d}-retry"
                        ),
                        options=retry_options,
                    )
                    self._merge_structured_retry_metadata(
                        retry_result=retry_result,
                        first_result=result,
                    )
                    return retry_result

            batch_results = await asyncio.gather(
                *[
                    parse_batch(index, contact_sheet)
                    for index, contact_sheet in enumerate(contact_sheets, start=1)
                ]
            )

        evidence = self._build_image_evidence(
            decision=decision,
            contact_sheets=contact_sheets,
            batch_results=batch_results,
            extraction_profile=extraction_profile,
        )
        text = self._build_evidence_markdown(
            evidence=evidence,
            batch_count=len(batch_results),
        )
        metadata = self._combine_batch_metadata(batch_results)
        actual_concurrency = min(concurrency, len(batch_results))
        metadata.update(
            {
                "batch_count": len(batch_results),
                "object_count": len(decision.objects),
                "ocr_region_count": len(decision.objects),
                "image_object_count": len(decision.objects),
                "image_batch_count": len(batch_results),
                "image_batch_size": batch_size,
                "image_multi_object_concurrency": actual_concurrency,
                "peak_concurrency": actual_concurrency,
                "image_evidence_schema_version": evidence["schema_version"],
                "image_extraction_profile": extraction_profile,
                "image_query_ready_count": evidence["summary"]["query_ready_count"],
                "image_review_required_count": evidence["summary"][
                    "review_required_count"
                ],
                "image_evidence": evidence,
                "image_assets": [
                    {
                        "asset_id": crop.object_id,
                        "object_id": crop.object_id,
                        "bbox": list(crop.bbox),
                        "local_path": str(crop.local_path) if crop.local_path else "",
                        "crop_ref": crop.crop_ref,
                    }
                    for contact_sheet in contact_sheets
                    for crop in contact_sheet.objects
                ],
            }
        )
        return TranscriptResult(
            text=text,
            backend=batch_results[0].backend,
            model=batch_results[0].model,
            metadata=metadata,
        )

    async def _transcribe_whole_image(
        self,
        *,
        file_path: Path,
        media_type: str,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
    ) -> TranscriptResult:
        return await self._backend.transcribe(
            file_path,
            media_type=media_type,
            file_type=file_type,
            source_file_name=source_file_name,
            options=options,
        )

    @staticmethod
    def _resolve_mode(options: dict[str, Any]) -> str:
        mode = str(
            options.get("image_parse_mode")
            or options.get("mode")
            or "auto"
        ).strip().lower()
        aliases = {
            "multi": "multi_object",
            "objects": "multi_object",
            "object_list": "multi_object",
            "single": "single_object",
        }
        normalized = aliases.get(mode, mode)
        supported = {"auto", "multi_object", *_WHOLE_IMAGE_MODES}
        if normalized not in supported:
            raise ValueError(f"Unsupported image_parse_mode: {mode}")
        return normalized

    @staticmethod
    def _scene_detection_prompt(*, forced_multi_object: bool) -> str:
        forced_instruction = (
            "调用方已经指定多对象模式；仍需检测每个独立对象并返回位置框。"
            if forced_multi_object
            else "先判断图片属于文档、单对象、多对象、图表或通用图片。"
        )
        return (
            "你是图片场景路由器，不负责回答用户业务问题。"
            f"{forced_instruction}"
            "只输出一个 JSON 对象，不要输出 Markdown 或解释。"
            "坐标使用 0 到 1000 的归一化整数，格式为 [x1,y1,x2,y2]。"
            "对于 multi_object，objects 必须覆盖图片中所有彼此独立、可单独识别的主体；"
            "对于其他场景，objects 可以为空。"
            'JSON schema: {"scene_type":"document|single_object|multi_object|chart|general",'
            '"confidence":0.0,"object_count_estimate":0,'
            '"objects":[{"id":"1","bbox":[0,0,1000,1000]}]}'
        )

    @staticmethod
    def _multi_object_prompt(
        *,
        user_intent: str,
        target_fields: tuple[str, ...],
        original_prompt: str,
        object_ids: tuple[str, ...],
        extraction_profile: str,
    ) -> str:
        fields = ", ".join(target_fields) if target_fields else "visible_text, color, shape"
        intent = user_intent or original_prompt or "逐一描述每个独立对象"
        expected_ids = ", ".join(object_ids)
        if extraction_profile != "pill_search":
            return (
                "这是一张由原图局部裁片组成的联系表，每个裁片左上角都有稳定的对象编号。"
                f"用户意图：{intent}\n"
                f"需要提取的字段：{fields}\n"
                f"必须返回且只能返回这些对象编号：{expected_ids}\n"
                "只记录裁片中直接可见的事实，不得根据常识补全。"
                "只输出 JSON，不要输出 Markdown 或解释。"
                'JSON schema: {"objects":[{"object_id":"pill-001",'
                '"visible_text":{"observed":"text","candidates":[],"confidence":0.9},'
                '"color":{"primary":["blue"],"confidence":0.9},'
                '"shape":{"value":"round","confidence":0.9},'
                '"evidence_notes":"visible evidence"}]}'
            )
        return (
            "这是一张由原图局部裁片组成的联系表，每个裁片左上角都有稳定的对象编号。"
            f"用户意图：{intent}\n"
            f"需要提取的字段：{fields}\n"
            f"必须返回且只能返回这些对象编号：{expected_ids}\n"
            "必须逐个编号输出结果，保留原始可见文字，不得根据常识补全、纠正或创造文字。"
            "只有字符轮廓清晰、逐字符置信度不低于 0.85 时才允许写入 raw_imprint 或 visible_text；"
            "否则 observed 必须写 NA，可把不确定读法放入 candidates。"
            "颜色使用英文基础颜色数组；shape 优先使用 Round、Oval、Capsule/Oblong、"
            "Square、Rectangle、Diamond、Triangle、Five-sided、Six-sided、Eight-sided、"
            "Egg-shape 或 U-shape。"
            "不得省略任何编号。"
            "只输出 JSON，不要输出 Markdown 或解释。"
            'JSON schema: {"objects":[{"object_id":"pill-001",'
            '"imprint":{"observed":"L 5","candidates":["L 6"],"confidence":0.92},'
            '"color":{"primary":["blue","red"],"confidence":0.98},'
            '"shape":{"value":"Capsule/Oblong","confidence":0.96},'
            '"dose_form_visual":"capsule","score_or_logo":false,'
            '"evidence_notes":"visible character evidence"}]}'
        )

    @classmethod
    def _parse_scene_decision(
        cls,
        text: str,
        *,
        forced_multi_object: bool,
        max_objects: int,
    ) -> ImageSceneDecision:
        payload = cls._extract_json_object(text)
        raw_scene_type = str(payload.get("scene_type") or "general").strip().lower()
        scene_type = "multi_object" if forced_multi_object else raw_scene_type
        if scene_type not in _SCENE_TYPES:
            scene_type = "general"
        objects: list[ImageObjectRegion] = []
        raw_objects = payload.get("objects") or []
        if isinstance(raw_objects, list):
            for index, raw_object in enumerate(raw_objects[:max_objects], start=1):
                region = cls._parse_region(
                    raw_object,
                    stable_id=f"pill-{index:03d}",
                )
                if region is not None:
                    objects.append(region)
        confidence = cls._bounded_float(
            payload.get("confidence"),
            default=0.0,
            minimum=0.0,
            maximum=1.0,
        )
        object_count = cls._positive_int(
            payload.get("object_count_estimate"),
            len(objects),
        )
        return ImageSceneDecision(
            scene_type=scene_type,
            confidence=confidence,
            object_count_estimate=max(object_count, len(objects)),
            objects=tuple(objects),
        )

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
        stripped = text.strip()
        fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
        candidate = fenced_match.group(1) if fenced_match else stripped
        if not candidate.startswith("{"):
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start >= 0 and end > start:
                candidate = candidate[start : end + 1]
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Image VLM returned invalid structured JSON")
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _parse_region(
        raw_object: Any,
        *,
        stable_id: str,
    ) -> ImageObjectRegion | None:
        if not isinstance(raw_object, dict):
            return None
        raw_bbox = raw_object.get("bbox")
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
            return None
        try:
            x1, y1, x2, y2 = [int(round(float(value))) for value in raw_bbox]
        except (TypeError, ValueError):
            return None
        x1, y1 = max(0, min(999, x1)), max(0, min(999, y1))
        x2, y2 = max(1, min(1000, x2)), max(1, min(1000, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return ImageObjectRegion(object_id=stable_id, bbox=(x1, y1, x2, y2))

    @classmethod
    def _build_contact_sheets(
        cls,
        *,
        file_path: Path,
        regions: tuple[ImageObjectRegion, ...],
        sheet_output_dir: Path,
        asset_output_dir: Path | None,
        batch_size: int,
        padding_ratio: float,
    ) -> list[ImageContactSheet]:
        sheet_output_dir.mkdir(parents=True, exist_ok=True)
        crop_output_dir = asset_output_dir or sheet_output_dir / "crops"
        crop_output_dir.mkdir(parents=True, exist_ok=True)
        crops: list[ImageObjectCrop] = []
        with Image.open(file_path) as image:
            source = ImageOps.exif_transpose(image).convert("RGB")
            for region in regions:
                crop = cls._crop_region(
                    source,
                    bbox=region.bbox,
                    padding_ratio=padding_ratio,
                )
                crop_path = crop_output_dir / f"{region.object_id}.png"
                crop.save(crop_path, format="PNG")
                crops.append(
                    ImageObjectCrop(
                        object_id=region.object_id,
                        bbox=region.bbox,
                        local_path=crop_path,
                        crop_ref=(
                            f"{crop_output_dir.name}/{crop_path.name}"
                            if asset_output_dir
                            else ""
                        ),
                    )
                )
        sheets: list[ImageContactSheet] = []
        for batch_index in range(0, len(crops), batch_size):
            batch_crops = tuple(crops[batch_index : batch_index + batch_size])
            sheet_path = (
                sheet_output_dir
                / f"objects-{batch_index // batch_size + 1:03d}.png"
            )
            cls._write_contact_sheet(
                batch_crops,
                output_path=sheet_path,
            )
            sheets.append(
                ImageContactSheet(
                    path=sheet_path,
                    objects=batch_crops,
                )
            )
        return sheets

    @staticmethod
    def _crop_region(
        image: Image.Image,
        *,
        bbox: tuple[int, int, int, int],
        padding_ratio: float,
    ) -> Image.Image:
        width, height = image.size
        x1, y1, x2, y2 = bbox
        left = x1 * width / 1000
        top = y1 * height / 1000
        right = x2 * width / 1000
        bottom = y2 * height / 1000
        pad_x = (right - left) * padding_ratio
        pad_y = (bottom - top) * padding_ratio
        crop_box = (
            max(0, math.floor(left - pad_x)),
            max(0, math.floor(top - pad_y)),
            min(width, math.ceil(right + pad_x)),
            min(height, math.ceil(bottom + pad_y)),
        )
        crop = image.crop(crop_box)
        if crop.height > crop.width * 1.2:
            crop = crop.rotate(90, expand=True)
        return crop

    @staticmethod
    def _write_contact_sheet(
        crops: tuple[ImageObjectCrop, ...],
        *,
        output_path: Path,
    ) -> None:
        tile_width, tile_height = 640, 600
        columns = 2
        rows = math.ceil(len(crops) / columns)
        sheet = Image.new("RGB", (tile_width * columns, tile_height * rows), "white")
        draw = ImageDraw.Draw(sheet)
        for index, crop_evidence in enumerate(crops):
            if crop_evidence.local_path is None:
                continue
            with Image.open(crop_evidence.local_path) as crop_image:
                original = crop_image.convert("RGB")
                enhanced = ImageEnhance.Sharpness(
                    ImageEnhance.Contrast(original.rotate(180, expand=True)).enhance(1.25)
                ).enhance(1.5)
                views = (
                    ("0°", original),
                    ("180° enhanced", enhanced),
                )
            column, row = index % columns, index // columns
            draw.rectangle(
                (
                    column * tile_width,
                    row * tile_height,
                    column * tile_width + tile_width - 1,
                    row * tile_height + tile_height - 1,
                ),
                outline="black",
                width=2,
            )
            draw.text(
                (column * tile_width + 12, row * tile_height + 12),
                crop_evidence.object_id,
                fill="black",
            )
            view_height = (tile_height - 72) // len(views)
            for view_index, (view_label, view_image) in enumerate(views):
                tile = ImageOps.contain(
                    view_image,
                    (tile_width - 40, view_height - 24),
                )
                view_top = row * tile_height + 48 + view_index * view_height
                left = column * tile_width + (tile_width - tile.width) // 2
                top = view_top + (view_height - tile.height) // 2
                sheet.paste(tile, (left, top))
                draw.text(
                    (column * tile_width + 12, view_top),
                    view_label,
                    fill="gray",
                )
        sheet.save(output_path, format="PNG")

    @staticmethod
    def _resolve_asset_output_dir(raw_path: Any) -> Path | None:
        normalized = str(raw_path or "").strip()
        if not normalized:
            return None
        output_dir = Path(normalized).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    @staticmethod
    def _resolve_extraction_profile(
        *,
        options: dict[str, Any],
        user_intent: str,
        target_fields: tuple[str, ...],
    ) -> str:
        explicit = str(options.get("image_extraction_profile") or "").strip().lower()
        aliases = {
            "medication": "pill_search",
            "medication_identification": "pill_search",
            "pill": "pill_search",
            "pill_identifier": "pill_search",
            "pill_search": "pill_search",
        }
        if explicit:
            return aliases.get(explicit, explicit)
        normalized_fields = {field.strip().lower() for field in target_fields}
        if normalized_fields.intersection(
            {"imprint", "markings", "raw_imprint", "dose_form", "dose_form_visual"}
        ):
            return "pill_search"
        normalized_intent = user_intent.lower()
        if any(
            keyword in normalized_intent
            for keyword in (
                "drug",
                "medication",
                "pharmac",
                "pill",
                "药品",
                "药片",
                "药物",
            )
        ):
            return "pill_search"
        return "generic_objects"

    @classmethod
    def _has_expected_objects(
        cls,
        text: str,
        expected_ids: tuple[str, ...],
    ) -> bool:
        payload = cls._extract_json_object(text)
        raw_objects = payload.get("objects")
        if not isinstance(raw_objects, list):
            return False
        actual_ids = {
            cls._normalize_object_id(item.get("object_id"))
            for item in raw_objects
            if isinstance(item, dict)
        }
        return set(expected_ids).issubset(actual_ids)

    @staticmethod
    def _merge_structured_retry_metadata(
        *,
        retry_result: TranscriptResult,
        first_result: TranscriptResult,
    ) -> None:
        retry_result.metadata.update(
            {
                "model_call_count": (
                    int(first_result.metadata.get("model_call_count") or 1)
                    + int(retry_result.metadata.get("model_call_count") or 1)
                ),
                "model_retry_count": (
                    int(first_result.metadata.get("model_retry_count") or 0)
                    + int(retry_result.metadata.get("model_retry_count") or 0)
                    + 1
                ),
                "model_wait_ms": (
                    int(first_result.metadata.get("model_wait_ms") or 0)
                    + int(retry_result.metadata.get("model_wait_ms") or 0)
                ),
                "structured_output_retry_count": 1,
            }
        )

    @classmethod
    def _build_image_evidence(
        cls,
        *,
        decision: ImageSceneDecision,
        contact_sheets: list[ImageContactSheet],
        batch_results: list[TranscriptResult],
        extraction_profile: str,
    ) -> dict[str, Any]:
        objects: list[dict[str, Any]] = []
        for contact_sheet, batch_result in zip(
            contact_sheets,
            batch_results,
            strict=True,
        ):
            raw_by_id = cls._index_batch_objects(batch_result.text)
            for crop in contact_sheet.objects:
                raw_object = raw_by_id.get(crop.object_id, {})
                objects.append(
                    cls._normalize_object_evidence(
                        crop=crop,
                        raw_object=raw_object,
                        extraction_profile=extraction_profile,
                    )
                )
        query_ready_count = sum(
            1 for item in objects if item.get("query_ready") is True
        )
        return {
            "schema_version": "image_visual_evidence/1.0",
            "extraction_profile": extraction_profile,
            "scene": {
                "type": decision.scene_type,
                "confidence": decision.confidence,
                "object_count_estimate": decision.object_count_estimate,
            },
            "summary": {
                "object_count": len(objects),
                "query_ready_count": query_ready_count,
                "review_required_count": len(objects) - query_ready_count,
            },
            "objects": objects,
        }

    @classmethod
    def _index_batch_objects(cls, text: str) -> dict[str, dict[str, Any]]:
        payload = cls._extract_json_object(text)
        raw_objects = payload.get("objects")
        if not isinstance(raw_objects, list):
            return {}
        indexed: dict[str, dict[str, Any]] = {}
        for raw_object in raw_objects:
            if not isinstance(raw_object, dict):
                continue
            object_id = cls._normalize_object_id(raw_object.get("object_id"))
            if object_id:
                indexed[object_id] = raw_object
        return indexed

    @staticmethod
    def _normalize_object_id(raw_value: Any) -> str:
        normalized = str(raw_value or "").strip().lower()
        match = re.search(r"(?:pill|object)?[\s_-]*0*(\d+)$", normalized)
        if not match:
            return normalized
        return f"pill-{int(match.group(1)):03d}"

    @classmethod
    def _normalize_object_evidence(
        cls,
        *,
        crop: ImageObjectCrop,
        raw_object: dict[str, Any],
        extraction_profile: str,
    ) -> dict[str, Any]:
        color = cls._normalize_color_evidence(raw_object.get("color"))
        shape = cls._normalize_shape_evidence(raw_object.get("shape"))
        evidence_notes = str(raw_object.get("evidence_notes") or "").strip()
        result: dict[str, Any] = {
            "object_id": crop.object_id,
            "bbox": list(crop.bbox),
            "crop_ref": crop.crop_ref or "NA",
            "color": color,
            "shape": shape,
            "evidence_notes": evidence_notes or "NA",
        }
        if extraction_profile == "pill_search":
            imprint = cls._normalize_text_evidence(
                raw_object.get("imprint")
                or raw_object.get("raw_imprint")
                or raw_object.get("visible_text")
            )
            result.update(
                {
                    "imprint": imprint,
                    "dose_form_visual": cls._normalize_optional_value(
                        raw_object.get("dose_form_visual")
                    ),
                    "score_or_logo": cls._normalize_score_or_logo(
                        raw_object.get("score_or_logo")
                    ),
                }
            )
            query_ready, review_reason = cls._pill_query_readiness(
                imprint=imprint,
                color=color,
                shape=shape,
            )
            result["query_ready"] = query_ready
            result["review_reason"] = review_reason
            result["search_input"] = cls._build_pill_search_input(
                imprint=imprint,
                color=color,
                shape=shape,
            )
            return result

        visible_text = cls._normalize_text_evidence(
            raw_object.get("visible_text")
            or raw_object.get("raw_text")
            or raw_object.get("text")
        )
        result["visible_text"] = visible_text
        result["query_ready"] = visible_text["observed"] != "NA"
        result["review_reason"] = (
            "NA" if result["query_ready"] else "visible_text_not_reliably_observed"
        )
        return result

    @classmethod
    def _normalize_text_evidence(cls, raw_value: Any) -> dict[str, Any]:
        if isinstance(raw_value, dict):
            observed = cls._normalize_optional_value(raw_value.get("observed"))
            raw_candidates = raw_value.get("candidates")
            confidence = cls._bounded_float(
                raw_value.get("confidence"),
                default=0.0,
                minimum=0.0,
                maximum=1.0,
            )
        else:
            observed = cls._normalize_optional_value(raw_value)
            raw_candidates = []
            confidence = 0.0
        candidates = [
            value
            for value in cls._normalize_string_list(raw_candidates)
            if value != observed
        ]
        return {
            "observed": observed,
            "candidates": candidates,
            "confidence": confidence,
        }

    @classmethod
    def _normalize_color_evidence(cls, raw_value: Any) -> dict[str, Any]:
        if isinstance(raw_value, dict):
            raw_primary = raw_value.get("primary")
            confidence = cls._bounded_float(
                raw_value.get("confidence"),
                default=0.0,
                minimum=0.0,
                maximum=1.0,
            )
        else:
            raw_primary = raw_value
            confidence = 0.0
        primary = [
            cls._normalize_color_name(value)
            for value in cls._normalize_string_list(raw_primary)
        ]
        return {
            "primary": [value for value in primary if value != "NA"],
            "confidence": confidence,
        }

    @classmethod
    def _normalize_shape_evidence(cls, raw_value: Any) -> dict[str, Any]:
        if isinstance(raw_value, dict):
            raw_shape = raw_value.get("value")
            confidence = cls._bounded_float(
                raw_value.get("confidence"),
                default=0.0,
                minimum=0.0,
                maximum=1.0,
            )
        else:
            raw_shape = raw_value
            confidence = 0.0
        normalized = cls._normalize_optional_value(raw_shape)
        shape = _PILL_SHAPES.get(normalized.lower(), normalized)
        return {
            "value": shape,
            "confidence": confidence,
        }

    @staticmethod
    def _normalize_score_or_logo(raw_value: Any) -> bool | str:
        if isinstance(raw_value, bool):
            return raw_value
        normalized = str(raw_value or "").strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        return "NA"

    @classmethod
    def _pill_query_readiness(
        cls,
        *,
        imprint: dict[str, Any],
        color: dict[str, Any],
        shape: dict[str, Any],
    ) -> tuple[bool, str]:
        reasons: list[str] = []
        if imprint["observed"] == "NA":
            reasons.append("imprint_not_observed")
        elif float(imprint["confidence"]) < 0.85:
            reasons.append("imprint_confidence_below_0.85")
        if not color["primary"]:
            reasons.append("color_not_observed")
        if shape["value"] == "NA":
            reasons.append("shape_not_observed")
        return (not reasons, "NA" if not reasons else ",".join(reasons))

    @staticmethod
    def _build_pill_search_input(
        *,
        imprint: dict[str, Any],
        color: dict[str, Any],
        shape: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "imprint": (
                imprint["observed"]
                if imprint["observed"] != "NA"
                and float(imprint["confidence"]) >= 0.85
                else "NA"
            ),
            "imprint_candidates": imprint["candidates"],
            "color": " / ".join(color["primary"]) if color["primary"] else "NA",
            "shape": shape["value"],
        }

    @classmethod
    def _normalize_string_list(cls, raw_value: Any) -> list[str]:
        if isinstance(raw_value, str):
            values = re.split(r"[,/|]+", raw_value)
        elif isinstance(raw_value, (list, tuple, set)):
            values = list(raw_value)
        else:
            values = []
        normalized: list[str] = []
        for value in values:
            item = cls._normalize_optional_value(value)
            if item != "NA" and item not in normalized:
                normalized.append(item)
        return normalized

    @staticmethod
    def _normalize_optional_value(raw_value: Any) -> str:
        normalized = re.sub(r"\s+", " ", str(raw_value or "")).strip()
        return "NA" if normalized.lower() in _UNKNOWN_VALUES else normalized

    @staticmethod
    def _normalize_color_name(raw_value: str) -> str:
        normalized = raw_value.strip().lower()
        aliases = {
            "grey": "Gray",
            "gray": "Gray",
            "clear": "Clear",
            "transparent": "Clear",
        }
        if not normalized:
            return "NA"
        return aliases.get(normalized, normalized.title())

    @staticmethod
    def _build_evidence_markdown(
        *,
        evidence: dict[str, Any],
        batch_count: int,
    ) -> str:
        summary = evidence["summary"]
        evidence_json = json.dumps(evidence, ensure_ascii=False, indent=2)
        return "\n".join(
            [
                "## 图片场景路由",
                "",
                "- 场景: multi_object",
                "- 处理流程: detect_crop_batch_vlm",
                f"- 提取配置: {evidence['extraction_profile']}",
                f"- 检测对象数: {summary['object_count']}",
                f"- 解析批次数: {batch_count}",
                f"- 可直接检索: {summary['query_ready_count']}",
                f"- 需要复核: {summary['review_required_count']}",
                "",
                "## 结构化视觉证据",
                "",
                "```json",
                evidence_json,
                "```",
            ]
        )

    @staticmethod
    def _normalize_target_fields(raw_fields: Any) -> tuple[str, ...]:
        if isinstance(raw_fields, str):
            return tuple(
                field.strip()
                for field in raw_fields.split(",")
                if field.strip()
            )
        if isinstance(raw_fields, (list, tuple)):
            return tuple(str(field).strip() for field in raw_fields if str(field).strip())
        return ()

    @staticmethod
    def _combine_batch_metadata(
        batch_results: list[TranscriptResult],
    ) -> dict[str, Any]:
        model_call_count = sum(
            int(result.metadata.get("model_call_count") or 1)
            for result in batch_results
        )
        retry_count = sum(
            int(result.metadata.get("model_retry_count") or 0)
            for result in batch_results
        )
        model_wait_ms = sum(
            int(result.metadata.get("model_wait_ms") or 0)
            for result in batch_results
        )
        return {
            "model_call_count": model_call_count,
            "model_retry_count": retry_count,
            "model_wait_ms": model_wait_ms,
            "peak_concurrency": len(batch_results),
        }

    @staticmethod
    def _merge_routing_metadata(
        result: TranscriptResult,
        *,
        decision: ImageSceneDecision,
        detection_result: TranscriptResult,
        selected_pipeline: str,
        mode: str,
        fallback_reason: str = "",
    ) -> None:
        detection_calls = int(detection_result.metadata.get("model_call_count") or 1)
        parse_calls = int(result.metadata.get("model_call_count") or 1)
        result.metadata.update(
            {
                "model_call_count": detection_calls + parse_calls,
                "batch_count": int(result.metadata.get("batch_count") or 1),
                "object_count": len(decision.objects),
                "ocr_region_count": len(decision.objects),
                "image_parse_mode": mode,
                "image_scene_type": decision.scene_type,
                "image_scene_confidence": decision.confidence,
                "image_object_count_estimate": decision.object_count_estimate,
                "image_object_count": len(decision.objects),
                "image_selected_pipeline": selected_pipeline,
                "image_scene_detection_call_count": detection_calls,
            }
        )
        if fallback_reason:
            result.metadata["image_routing_fallback_reason"] = fallback_reason

    @staticmethod
    def _fallback_reason(decision: ImageSceneDecision, mode: str) -> str:
        if mode == "multi_object" and len(decision.objects) < 2:
            return "multi_object_detection_returned_fewer_than_two_valid_regions"
        if decision.scene_type == "multi_object":
            return "multi_object_detection_returned_fewer_than_two_valid_regions"
        return "scene_uses_whole_image_pipeline"

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _bounded_float(
        value: Any,
        *,
        default: float,
        minimum: float,
        maximum: float,
    ) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, parsed))
