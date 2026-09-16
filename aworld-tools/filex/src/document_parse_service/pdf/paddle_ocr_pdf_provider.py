"""PDF provider backed by PaddleOCR's official PaddleOCR-VL pipeline."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import re
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from html import escape, unescape
from pathlib import Path
from typing import Any

from ..document_artifact_models import DocumentAnchor, DocumentAsset, MarkdownArtifact
from ..paths import DOCUMENT_PARSE_WORKSPACE
from .text_layer_formatting import (
    document_ir_spans,
    extract_text_layer_spans,
    overlay_text_layer_formatting,
)

logger = logging.getLogger(__name__)
_SHARED_PIPELINES: dict[str, Any] = {}
_SHARED_PIPELINES_LOCK = threading.Lock()


@dataclass(slots=True)
class PaddleOcrPdfResult:
    """Normalized output of PaddleOCR-VL PDF parsing."""

    provider: str
    tool: str
    source_file_name: str
    markdown_text: str
    assets: list[DocumentAsset] = field(default_factory=list)
    page_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    total_elapsed_ms: float = 0
    initialization_elapsed_ms: float = 0
    parse_elapsed_ms: float = 0
    model_info: dict[str, Any] = field(default_factory=dict)
    raw_result_count: int = 0
    model_call_count: int = 0
    peak_concurrency: int = 0
    retry_count: int = 0
    provider_version: str = ""
    first_batch_elapsed_ms: float = 0
    document_ir: dict[str, Any] | None = None


class PaddleOcrPdfProvider:
    """Use PaddleOCR's official PaddleOCR-VL document parsing pipeline."""

    name = "paddle_ocr"

    def __init__(self, *, env_content: dict[str, Any] | None = None, pipeline: Any | None = None) -> None:
        self._env_content = env_content or {}
        self._pipeline = pipeline

    async def understand_pdf(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
    ) -> PaddleOcrPdfResult:
        started_at = time.monotonic()
        return await asyncio.to_thread(
            self._understand_pdf_sync,
            file_path,
            task_id,
            source_file_name,
            started_at,
        )

    def _understand_pdf_sync(
        self,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        started_at: float,
    ) -> PaddleOcrPdfResult:
        pipeline = self._resolve_pipeline()
        initialization_elapsed_ms = round((time.monotonic() - started_at) * 1000, 2)
        parse_started_at = time.monotonic()
        predict_kwargs = self._predict_kwargs()
        raw_results, retry_count, first_batch_elapsed_ms = self._predict_with_retries(
            pipeline,
            file_path=file_path,
            predict_kwargs=predict_kwargs,
            started_at=parse_started_at,
        )
        markdown_parts = [self._extract_markdown_data(result) for result in raw_results]
        markdown_text = self._concatenate_markdown(pipeline, markdown_parts)
        text_layer_pages = (
            extract_text_layer_spans(file_path)
            if self._bool_option("text_layer_formatting", False)
            else []
        )
        if text_layer_pages:
            markdown_text = overlay_text_layer_formatting(markdown_text, text_layer_pages)
        assets = self._write_markdown_images(
            markdown_parts,
            task_id=task_id,
            source_file_name=source_file_name,
        )
        document_ir = self._build_document_ir(raw_results, text_layer_pages=text_layer_pages)

        return PaddleOcrPdfResult(
            provider="paddle_ocr",
            tool="paddleocr_vl",
            source_file_name=source_file_name,
            markdown_text=markdown_text,
            assets=assets,
            page_count=self._resolve_page_count(raw_results),
            total_elapsed_ms=round((time.monotonic() - started_at) * 1000, 2),
            initialization_elapsed_ms=initialization_elapsed_ms,
            parse_elapsed_ms=round((time.monotonic() - parse_started_at) * 1000, 2),
            model_info=self._model_info(),
            raw_result_count=len(raw_results),
            model_call_count=self._model_call_count(raw_results),
            peak_concurrency=int(self._option("vl_rec_max_concurrency") or 0),
            retry_count=retry_count,
            provider_version=self._provider_version(),
            first_batch_elapsed_ms=first_batch_elapsed_ms,
            document_ir=document_ir,
        )

    def to_markdown_artifact(self, result: PaddleOcrPdfResult) -> MarkdownArtifact:
        markdown_text = result.markdown_text.strip()
        if not markdown_text:
            markdown_text = f"# {result.source_file_name}\n"

        return MarkdownArtifact(
            markdown_text=markdown_text.rstrip() + "\n",
            assets=result.assets,
            document_ir=result.document_ir,
            diagnostics={
                "provider": result.provider,
                "tool": result.tool,
                "page_count": result.page_count,
                "ocr_page_count": result.page_count,
                "vlm_page_count": result.page_count,
                "rendered_page_count": result.page_count,
                "asset_count": len(result.assets),
                "error_count": len(result.errors),
                "errors": result.errors,
                "total_elapsed_ms": result.total_elapsed_ms,
                "initialization_elapsed_ms": result.initialization_elapsed_ms,
                "parse_elapsed_ms": result.parse_elapsed_ms,
                "first_batch_duration_ms": result.first_batch_elapsed_ms,
                "model_info": result.model_info,
                "raw_result_count": result.raw_result_count,
                "model_call_count": result.model_call_count,
                "peak_concurrency": result.peak_concurrency,
                "model_retry_count": result.retry_count,
                "provider_version": result.provider_version,
                "document_ir_schema_version": (
                    str((result.document_ir or {}).get("schema_version") or "")
                ),
                "text_length": len(result.markdown_text),
            },
        )

    @classmethod
    def _build_document_ir(
        cls,
        raw_results: list[Any],
        *,
        text_layer_pages: list[list[Any]] | None = None,
    ) -> dict[str, Any]:
        """Normalize PaddleOCR page/block geometry into a stable FileX contract."""

        pages: list[dict[str, Any]] = []
        for fallback_index, result in enumerate(raw_results):
            payload = cls._json_payload(result)
            blocks = cls._parsed_blocks(payload)
            page_height = cls._numeric_dimension(payload.get("height"))
            elements = cls._layout_elements(
                payload,
                blocks,
                fallback_index,
                page_height=page_height,
            )
            page_index = payload.get("page_index")
            pages.append(
                {
                    "page_index": (
                        int(page_index)
                        if isinstance(page_index, (int, float))
                        else fallback_index
                    ),
                    "width": cls._numeric_dimension(payload.get("width")),
                    "height": cls._numeric_dimension(payload.get("height")),
                    "elements": elements,
                    "spans": document_ir_spans(text_layer_pages or [], fallback_index),
                }
            )
        return {
            "schema_version": "filex-document-ir-v3",
            "coordinate_system": "pixel_top_left_xyxy",
            "pages": pages,
        }

    @classmethod
    def _layout_elements(
        cls,
        payload: dict[str, Any],
        parsed_blocks: list[dict[str, Any]],
        page_index: int,
        *,
        page_height: float | None,
    ) -> list[dict[str, Any]]:
        """Keep detector geometry and enrich it with parser text/order metadata.

        PaddleOCR-VL may merge adjacent detector regions before VLM parsing.  The
        merged regions are useful for Markdown, but they erase small layout
        objects and are therefore unsuitable as the canonical grounding output.
        """

        raw_layout = payload.get("layout_det_res")
        raw_boxes = raw_layout.get("boxes") if isinstance(raw_layout, dict) else None
        if not isinstance(raw_boxes, list) or not raw_boxes:
            return [
                cls._parsed_element(block, page_index, order, page_height=page_height)
                for order, block in enumerate(parsed_blocks, start=1)
            ]

        elements: list[dict[str, Any]] = []
        matched_parsed: set[int] = set()
        for order, raw_box in enumerate(raw_boxes, start=1):
            if not isinstance(raw_box, dict):
                continue
            bbox = cls._bbox(raw_box.get("coordinate") or raw_box.get("bbox"))
            if bbox is None:
                continue
            match_index = cls._best_text_match(bbox, parsed_blocks)
            parsed = parsed_blocks[match_index] if match_index is not None else {}
            if match_index is not None:
                matched_parsed.add(match_index)
            parsed_order = parsed.get("block_order")
            confidence = raw_box.get("score")
            element = {
                "id": str(
                    raw_box.get("id")
                    or f"p{page_index}-d{order}"
                ),
                "type": cls._normalize_layout_label(
                    raw_box.get("label") or parsed.get("block_label"),
                    bbox=bbox,
                    page_height=page_height,
                ),
                "bbox": bbox,
                "text": cls._plain_text(
                    parsed.get("block_content") or parsed.get("content")
                ),
                "reading_order": (
                    int(parsed_order)
                    if isinstance(parsed_order, (int, float))
                    else None
                ),
                "group_id": parsed.get("global_group_id") or parsed.get("group_id"),
                "source": "layout_detection",
            }
            if isinstance(confidence, (int, float)):
                element["confidence"] = float(confidence)
            elements.append(element)

        # Preserve parser-only blocks such as generated tables/formulas when no
        # detector region represents them.  Do not duplicate blocks already used
        # to enrich a detector prediction.
        for index, block in enumerate(parsed_blocks):
            if index not in matched_parsed:
                elements.append(
                    cls._parsed_element(
                        block,
                        page_index,
                        index + 1,
                        page_height=page_height,
                    )
                )
        return elements

    @classmethod
    def _parsed_blocks(cls, payload: dict[str, Any]) -> list[dict[str, Any]]:
        blocks = payload.get("parsing_res_list")
        if not isinstance(blocks, list):
            return []
        return [block for block in blocks if isinstance(block, dict) and cls._bbox(
            block.get("block_bbox") or block.get("bbox")
        ) is not None]

    @classmethod
    def _parsed_element(
        cls,
        block: dict[str, Any],
        page_index: int,
        fallback_order: int,
        *,
        page_height: float | None,
    ) -> dict[str, Any]:
        order = block.get("block_order")
        bbox = cls._bbox(block.get("block_bbox") or block.get("bbox"))
        return {
            "id": str(
                block.get("global_block_id")
                or block.get("block_id")
                or f"p{page_index}-b{fallback_order}"
            ),
            "type": cls._normalize_layout_label(
                block.get("block_label") or block.get("label"),
                bbox=bbox,
                page_height=page_height,
            ),
            "bbox": bbox,
            "text": cls._plain_text(block.get("block_content") or block.get("content")),
            "reading_order": int(order) if isinstance(order, (int, float)) else None,
            "group_id": block.get("global_group_id") or block.get("group_id"),
            "source": "document_parsing",
        }

    @staticmethod
    def _plain_text(value: Any) -> str:
        """Return semantic block text without Markdown/HTML presentation tokens."""

        text = str(value or "")
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"!\[([^]]*)\]\([^)]+\)", r" \1 ", text)
        text = re.sub(r"\[([^]]+)\]\([^)]+\)", r" \1 ", text)
        text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
        return re.sub(r"\s+", " ", unescape(text)).strip()

    @staticmethod
    def _normalize_layout_label(
        value: Any,
        *,
        bbox: list[float] | None,
        page_height: float | None,
    ) -> str:
        label = str(value or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "figure": "image",
            "picture": "image",
            "footer_image": "image",
            "header_image": "image",
            "caption": "figure_title",
            "title": "paragraph_title",
            "page_footer": "footer",
            "footer_text": "footer",
            "page_header": "header",
            "header_text": "header",
        }
        label = aliases.get(label, label)
        if label in {"number", "page_number"} and bbox and page_height:
            if bbox[1] >= page_height * 0.9:
                return "footer"
            if bbox[3] <= page_height * 0.1:
                return "header"
        return label

    @staticmethod
    def _bbox(value: Any) -> list[float] | None:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            bbox = [float(item) for item in value]
        except (TypeError, ValueError):
            return None
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return None
        return bbox

    @classmethod
    def _best_text_match(
        cls,
        detector_bbox: list[float],
        parsed_blocks: list[dict[str, Any]],
    ) -> int | None:
        best_index: int | None = None
        best_iou = 0.0
        for index, block in enumerate(parsed_blocks):
            parsed_bbox = cls._bbox(block.get("block_bbox") or block.get("bbox"))
            if parsed_bbox is None:
                continue
            iou = cls._bbox_iou(detector_bbox, parsed_bbox)
            if iou > best_iou:
                best_iou = iou
                best_index = index
        # A strict IoU avoids copying one merged parser region's text into each
        # of several small detector boxes contained by that region.
        return best_index if best_iou >= 0.5 else None

    @staticmethod
    def _bbox_iou(left: list[float], right: list[float]) -> float:
        x1 = max(left[0], right[0])
        y1 = max(left[1], right[1])
        x2 = min(left[2], right[2])
        y2 = min(left[3], right[3])
        intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if intersection <= 0:
            return 0.0
        left_area = (left[2] - left[0]) * (left[3] - left[1])
        right_area = (right[2] - right[0]) * (right[3] - right[1])
        return intersection / (left_area + right_area - intersection)

    @staticmethod
    def _json_payload(result: Any) -> dict[str, Any]:
        json_value = getattr(result, "json", None)
        if callable(json_value):
            json_value = json_value()
        if isinstance(json_value, dict):
            nested = json_value.get("res")
            return nested if isinstance(nested, dict) else json_value
        if isinstance(result, dict):
            return result
        return {}

    @staticmethod
    def _numeric_dimension(value: Any) -> float | None:
        if isinstance(value, list) and value:
            value = value[0]
        return float(value) if isinstance(value, (int, float)) else None

    def _resolve_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        try:
            from paddleocr import PaddleOCRVL
        except ImportError as exc:
            raise RuntimeError(
                "paddle_ocr PDF provider requires paddleocr with PaddleOCRVL support. "
                "Install paddleocr in the filesystem_server runtime or choose another pdf_parse_provider."
            ) from exc

        kwargs = self._pipeline_kwargs()
        cache_key = json.dumps(kwargs, ensure_ascii=False, sort_keys=True, default=str)
        with _SHARED_PIPELINES_LOCK:
            self._pipeline = _SHARED_PIPELINES.get(cache_key)
            if self._pipeline is None:
                self._pipeline = PaddleOCRVL(**kwargs)
                _SHARED_PIPELINES[cache_key] = self._pipeline
        return self._pipeline

    @staticmethod
    def _model_call_count(raw_results: list[Any]) -> int:
        count = 0
        for result in raw_results:
            try:
                blocks = result.get("parsing_res_list")
            except (AttributeError, TypeError):
                blocks = None
            if isinstance(blocks, list):
                count += len(blocks)
        return count

    @staticmethod
    def _provider_version() -> str:
        try:
            return importlib.metadata.version("paddleocr")
        except importlib.metadata.PackageNotFoundError:
            return ""

    def _pipeline_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "vl_rec_backend": self._str_option("vl_rec_backend", "vllm-server"),
        }
        for key in (
            "pipeline_version",
            "layout_detection_model_name",
            "layout_detection_model_dir",
            "layout_threshold",
            "layout_nms",
            "layout_unclip_ratio",
            "layout_merge_bboxes_mode",
            "vl_rec_model_name",
            "vl_rec_model_dir",
            "vl_rec_backend",
            "vl_rec_server_url",
            "vl_rec_max_concurrency",
            "vl_rec_api_model_name",
            "vl_rec_api_key",
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_layout_detection",
            "use_chart_recognition",
            "use_seal_recognition",
            "use_ocr_for_image_block",
            "format_block_content",
            "merge_layout_blocks",
            "markdown_ignore_labels",
            "use_queues",
        ):
            value = self._option(key)
            if value not in (None, ""):
                kwargs[key] = value
        gateway_vllm = self._gateway_vllm_config()
        kwargs.setdefault("vl_rec_max_concurrency", self._option("vlm_max_concurrency") or 1)
        kwargs.setdefault("vl_rec_server_url", gateway_vllm.get("base_url"))
        kwargs.setdefault("vl_rec_api_model_name", gateway_vllm.get("model_name") or gateway_vllm.get("http_model_name"))
        kwargs.setdefault("vl_rec_api_key", gateway_vllm.get("api_key") or self._resolve_gateway_vllm_api_key())
        return {key: value for key, value in kwargs.items() if value not in (None, "")}

    def _predict_with_retries(
        self,
        pipeline: Any,
        *,
        file_path: Path,
        predict_kwargs: dict[str, Any],
        started_at: float,
    ) -> tuple[list[Any], int, float]:
        max_retries = max(0, int(self._option("vlm_max_retries") or 3))
        retry_count = 0
        while True:
            try:
                raw_results = []
                first_batch_elapsed_ms = 0.0
                for raw_result in pipeline.predict(str(file_path), **predict_kwargs):
                    raw_results.append(raw_result)
                    if not first_batch_elapsed_ms:
                        first_batch_elapsed_ms = round((time.monotonic() - started_at) * 1000, 2)
                return raw_results, retry_count, first_batch_elapsed_ms
            except Exception as exc:  # noqa: BLE001
                if retry_count >= max_retries or not self._is_retryable_error(exc):
                    raise
                retry_count += 1
                delay_ms = self._retry_delay_ms(retry_count)
                logger.warning(
                    "paddle_ocr provider retrying transient VLM failure | retry=%s max_retries=%s "
                    "delay_ms=%s error=%s",
                    retry_count,
                    max_retries,
                    delay_ms,
                    exc,
                )
                time.sleep(delay_ms / 1000)

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            signal in message
            for signal in ("429", "rate limit", "rpm_limit", "额度超限", "限流", "timeout", "timed out", "502", "503", "504")
        )

    def _retry_delay_ms(self, retry_count: int) -> int:
        base_ms = max(0, int(self._option("vlm_retry_base_delay_ms") or 500))
        max_ms = max(base_ms, int(self._option("vlm_retry_max_delay_ms") or 8000))
        return min(max_ms, base_ms * (2 ** max(0, retry_count - 1)))

    def _predict_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        for key in (
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_layout_detection",
            "use_chart_recognition",
            "use_seal_recognition",
            "use_ocr_for_image_block",
            "layout_threshold",
            "layout_nms",
            "layout_unclip_ratio",
            "layout_merge_bboxes_mode",
            "layout_shape_mode",
            "prompt_label",
            "format_block_content",
            "repetition_penalty",
            "temperature",
            "top_p",
            "min_pixels",
            "max_pixels",
            "max_new_tokens",
            "merge_layout_blocks",
            "markdown_ignore_labels",
        ):
            value = self._option(key)
            if value not in (None, ""):
                kwargs[key] = value
        vlm_extra_args = self._option("vlm_extra_args")
        if isinstance(vlm_extra_args, dict):
            kwargs["vlm_extra_args"] = vlm_extra_args
        return kwargs

    @staticmethod
    def _extract_markdown_data(result: Any) -> dict[str, Any]:
        markdown = getattr(result, "markdown", None)
        if isinstance(markdown, dict):
            return markdown
        if callable(markdown):
            markdown = markdown()
            if isinstance(markdown, dict):
                return markdown
        to_markdown = getattr(result, "_to_markdown", None)
        if callable(to_markdown):
            markdown = to_markdown()
            if isinstance(markdown, dict):
                return markdown
        if isinstance(result, dict):
            return result
        return {"markdown_texts": str(result or "")}

    @staticmethod
    def _concatenate_markdown(pipeline: Any, markdown_parts: list[dict[str, Any]]) -> str:
        if not markdown_parts:
            return ""
        concatenate = getattr(pipeline, "concatenate_markdown_pages", None)
        if callable(concatenate):
            try:
                merged = concatenate(markdown_parts)
                if isinstance(merged, str):
                    return merged.strip()
                if isinstance(merged, dict):
                    return str(merged.get("markdown_texts") or "").strip()
            except Exception:  # noqa: BLE001
                logger.debug("paddle_ocr_vl concatenate_markdown_pages failed", exc_info=True)
        return "\n\n".join(str(part.get("markdown_texts") or "").strip() for part in markdown_parts if part.get("markdown_texts")).strip()

    def _write_markdown_images(
        self,
        markdown_parts: list[dict[str, Any]],
        *,
        task_id: str,
        source_file_name: str,
    ) -> list[DocumentAsset]:
        output_dir = DOCUMENT_PARSE_WORKSPACE / task_id / "paddleocr_vl_images"
        assets: list[DocumentAsset] = []
        image_index = 0
        for page_index, markdown_data in enumerate(markdown_parts, start=1):
            images = self._extract_images_dict(markdown_data)
            for markdown_path, image_data in images.items():
                image_index += 1
                image_path = self._resolve_image_path(
                    output_dir=output_dir,
                    source_file_name=source_file_name,
                    page_index=page_index,
                    image_index=image_index,
                    markdown_path=str(markdown_path),
                )
                self._save_image(image_path, image_data)
                assets.append(
                    DocumentAsset(
                        asset_id=f"paddle_ocr_vl_image_{image_index}",
                        kind="embedded_image",
                        local_path=image_path,
                        page_number=page_index,
                        order=image_index,
                        anchor=DocumentAnchor(page_number=page_index),
                        meta={
                            "index": str(image_index),
                            "name": image_path.name,
                            "local_path": str(image_path),
                            "markdown_path": self._markdown_path(image_path),
                            "original_markdown_path": str(markdown_path),
                            "placement": "already_in_markdown",
                        },
                    )
                )
        return assets

    @staticmethod
    def _extract_images_dict(markdown_data: dict[str, Any]) -> dict[str, Any]:
        for key in ("markdown_images", "images", "imgs", "image"):
            value = markdown_data.get(key)
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _resolve_image_path(
        *,
        output_dir: Path,
        source_file_name: str,
        page_index: int,
        image_index: int,
        markdown_path: str,
    ) -> Path:
        suffix = Path(markdown_path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            suffix = ".png"
        return output_dir / f"{source_file_name}-p{page_index}-{image_index}{suffix}"

    @staticmethod
    def _save_image(image_path: Path, image_data: Any) -> None:
        image_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(image_data, bytes):
            image_path.write_bytes(image_data)
            return
        if hasattr(image_data, "save"):
            image_data.save(image_path)
            return
        try:
            import numpy as np
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Saving PaddleOCR-VL markdown images requires pillow and numpy") from exc
        if isinstance(image_data, np.ndarray):
            Image.fromarray(image_data).save(image_path)
            return
        raise RuntimeError(f"Unsupported PaddleOCR-VL markdown image type: {type(image_data)!r}")

    @staticmethod
    def _resolve_page_count(raw_results: list[Any]) -> int:
        page_indexes: set[int] = set()
        for index, result in enumerate(raw_results, start=1):
            getter = getattr(result, "get", None)
            page_index = getter("page_index") if callable(getter) else None
            if page_index is None and isinstance(result, dict):
                page_index = result.get("page_index")
            try:
                page_indexes.add(int(page_index) + 1)
            except (TypeError, ValueError):
                page_indexes.add(index)
        return max(page_indexes) if page_indexes else len(raw_results)

    @staticmethod
    def replace_markdown_asset_references(markdown_text: str, assets: list[DocumentAsset]) -> str:
        updated = markdown_text
        for asset in assets:
            original = str(asset.meta.get("original_markdown_path") or "").strip()
            target = str(
                asset.meta.get("remote_url")
                or asset.remote_id
                or asset.meta.get("markdown_path")
                or asset.meta.get("local_path")
                or ""
            ).strip()
            if not original or not target or original == target:
                continue
            updated = PaddleOcrPdfProvider._replace_markdown_image_reference(
                updated,
                original=original,
                target=target,
                file_id=asset.remote_id,
            )
            updated = PaddleOcrPdfProvider._replace_html_image_reference(
                updated,
                original=original,
                target=target,
                file_id=asset.remote_id,
            )
        return updated

    @staticmethod
    def _replace_markdown_image_reference(
        markdown_text: str,
        *,
        original: str,
        target: str,
        file_id: str,
    ) -> str:
        pattern = re.compile(r"!\[([^\]]*)\]\(" + re.escape(original) + r"\)")

        def replace(match: re.Match[str]) -> str:
            alt = match.group(1)
            if not file_id:
                return f"![{alt}]({target})"
            return (
                f'<img src="{escape(target, quote=True)}" '
                f'data-file-id="{escape(file_id, quote=True)}" '
                f'alt="{escape(alt, quote=True)}" />'
            )

        return pattern.sub(replace, markdown_text)

    @staticmethod
    def _replace_html_image_reference(
        markdown_text: str,
        *,
        original: str,
        target: str,
        file_id: str,
    ) -> str:
        updated = markdown_text.replace(f'src="{original}"', f'src="{target}"')
        updated = updated.replace(f"src='{original}'", f"src='{target}'")
        if not file_id:
            return updated
        escaped_file_id = escape(file_id, quote=True)
        updated = re.sub(
            r"(<img\b(?![^>]*\bdata-file-id=)[^>]*\bsrc=\"" + re.escape(target) + r"\"[^>]*)(/?>)",
            rf'\1 data-file-id="{escaped_file_id}"\2',
            updated,
        )
        updated = re.sub(
            r"(<img\b(?![^>]*\bdata-file-id=)[^>]*\bsrc='" + re.escape(target) + r"'[^>]*)(/?>)",
            rf'\1 data-file-id="{escaped_file_id}"\2',
            updated,
        )
        return updated

    @staticmethod
    def _markdown_path(image_path: Path) -> str:
        try:
            return str(image_path.relative_to(DOCUMENT_PARSE_WORKSPACE))
        except ValueError:
            return str(image_path)

    def _model_info(self) -> dict[str, Any]:
        keys = (
            "pipeline_version",
            "vl_rec_backend",
            "vl_rec_server_url",
            "vl_rec_api_model_name",
            "use_layout_detection",
            "use_ocr_for_image_block",
            "merge_layout_blocks",
        )
        return {key: self._option(key) for key in keys if self._option(key) not in (None, "")}

    def _option(self, key: str) -> Any:
        for candidate in (f"pdf_paddle_ocr_{key}", f"paddle_ocr_{key}", f"pdf_{key}", key):
            if candidate in self._env_content and self._env_content[candidate] not in (None, ""):
                return self._env_content[candidate]
        return None

    def _str_option(self, key: str, default: str) -> str:
        value = self._option(key)
        if value in (None, ""):
            return default
        return str(value)

    def _bool_option(self, key: str, default: bool) -> bool:
        value = self._option(key)
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _gateway_vllm_config(self) -> dict[str, Any]:
        config = self._env_content.get("gateway_vllm")
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _resolve_gateway_vllm_api_key() -> str:
        for env_name in ("GATEWAY_VLLM_API_KEY", "OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY"):
            value = os.getenv(env_name)
            if value:
                return value
        return ""


# Backward-compatible aliases for tests/imports that used the previous provider shape.
PaddleOcrLine = None
PaddleOcrPage = None
