"""
PDF 图表/矢量图截图提取服务。

使用 `pdftohtml -xml` 的 caption 坐标和 `pdftocairo` 的页面渲染结果，
从 PDF 中裁剪 Figure/Table 等区域截图，覆盖非嵌入图片的矢量图场景。
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]
from PIL import Image
import requests

from ..paths import DOCUMENT_PARSE_WORKSPACE
from ..pdf_layout_extract_service import PdfLayoutExtractService


logger = logging.getLogger(__name__)


class PdfFigureExtractService:
    """封装 PDF Figure/Table 截图提取逻辑。"""

    _CAPTION_PATTERN = re.compile(
        r"^(Figure|Fig\.|Chart)\s+\d+[A-Za-z]?(?:\s+.*)?$",
        re.IGNORECASE,
    )

    def __init__(
        self,
        workspace_base: Path | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        figure_locate_url: str | None = None,
        request_post: Callable[..., Any] | None = None,
    ) -> None:
        self._workspace_base = workspace_base or DOCUMENT_PARSE_WORKSPACE
        self._command_runner = command_runner or subprocess.run
        self._figure_locate_url = (
            figure_locate_url
            or os.getenv("LITEPARSE_FIGURE_LOCATE_URL")
            or os.getenv("liteparse_figure_locate_url")
        )
        self._request_post = request_post or requests.post

    def extract_from_pdf(
        self,
        file_path: Path,
        *,
        task_id: str,
        source_file_name: str,
        password: str | None = None,
        dpi: int = 200,
    ) -> dict[str, Any]:
        if not file_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {file_path}")
        if file_path.suffix.lower() != ".pdf":
            raise ValueError(f"仅支持 PDF 图表截图提取: {file_path}")

        output_dir = self._workspace_base / task_id / "pdf_figures"
        render_dir = self._workspace_base / task_id / "pdf_rendered_pages"
        self._prepare_output_dir(output_dir)
        self._prepare_output_dir(render_dir)

        layout_service = PdfLayoutExtractService(
            workspace_base=self._workspace_base,
            command_runner=self._command_runner,
        )
        layout_result = layout_service.extract_from_pdf(
            file_path=file_path,
            task_id=task_id,
            source_file_name=source_file_name,
            password=password,
        )

        render_prefix = render_dir / f"{source_file_name}_page"
        rendered_pages = self._render_pdf_pages(
            file_path=file_path,
            render_prefix=render_prefix,
            password=password,
            dpi=dpi,
        )

        figures: list[dict[str, Any]] = []
        for page in layout_result["pages"]:
            page_number = int(page["page_number"])
            rendered_page_path = rendered_pages.get(page_number)
            if not rendered_page_path or not rendered_page_path.exists():
                continue

            with Image.open(rendered_page_path) as page_image:
                image_width, image_height = page_image.size
                scale_x = image_width / max(int(page.get("width", 0)) or 1, 1)
                scale_y = image_height / max(int(page.get("height", 0)) or 1, 1)
                images = page.get("images", [])
                figure_specs = self._build_all_region_specs_with_vlm(
                    page=page,
                    page_image=page_image,
                    scale_x=scale_x,
                    scale_y=scale_y,
                )
                if not figure_specs:
                    figure_specs = self._build_figure_specs(page)
                if not figure_specs:
                    continue

                for figure_index, spec in enumerate(figure_specs, start=1):
                    if "crop_box" in spec:
                        crop_box = spec["crop_box"]
                        bbox = spec["figure_bbox"]
                        locator = str(spec.get("locator", "vlm_all"))
                    else:
                        crop_box, bbox, locator = self._resolve_crop_box(
                            page=page,
                            page_image=page_image,
                            spec=spec,
                            scale_x=scale_x,
                            scale_y=scale_y,
                        )
                    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
                        continue
                    if locator == "heuristic" and self._has_embedded_image_in_region(images, bbox):
                        continue

                    cropped_image = page_image.crop(crop_box)
                    output_path = output_dir / f"{source_file_name}_page{page_number}_figure{figure_index}.png"
                    cropped_image.save(output_path, format="PNG")
                    figures.append(
                        {
                            "page_number": page_number,
                            "path": output_path,
                            "name": output_path.name,
                            "caption": spec["caption_text"],
                            "figure_bbox": bbox,
                            "caption_bbox": spec["caption_bbox"],
                            "before_snippets": spec["before_snippets"],
                            "after_snippets": spec["after_snippets"],
                            "locator": locator,
                        }
                    )

        logger.info(
            "pdf_figure_extract_service extracted figures | file_path=%s task_id=%s figure_count=%s output_dir=%s",
            file_path,
            task_id,
            len(figures),
            output_dir,
        )
        return {
            "figure_count": len(figures),
            "figures": figures,
            "output_dir": output_dir,
            "render_dir": render_dir,
            "layout_xml_path": layout_result["xml_path"],
        }

    def _build_all_region_specs_with_vlm(
        self,
        *,
        page: dict[str, Any],
        page_image: Image.Image,
        scale_x: float,
        scale_y: float,
    ) -> list[dict[str, Any]]:
        if not self._figure_locate_url:
            return []

        regions = self._request_vlm_regions(
            page_image=page_image,
            hint="Extract all figures, charts, plots, screenshots, photos, and diagrams on this page.",
            max_regions=12,
            detect_mode="all",
        )
        if not regions:
            return []

        page_width = page_image.size[0]
        page_height = page_image.size[1]
        captions = self._find_caption_groups(page.get("texts", []))
        specs: list[dict[str, Any]] = []

        for region in regions:
            bbox = region.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            try:
                crop_box = tuple(int(round(float(value))) for value in bbox)
            except (TypeError, ValueError):
                continue

            crop_box = (
                max(0, min(page_width, crop_box[0])),
                max(0, min(page_height, crop_box[1])),
                max(0, min(page_width, crop_box[2])),
                max(0, min(page_height, crop_box[3])),
            )
            if crop_box[2] - crop_box[0] < 60 or crop_box[3] - crop_box[1] < 60:
                continue

            layout_bbox = self._image_crop_box_to_layout_bbox(
                crop_box=crop_box,
                page=page,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            if self._looks_like_full_page_region(layout_bbox, page):
                continue

            caption = self._find_caption_for_region(captions, layout_bbox)
            region_top = int(layout_bbox["top"])
            before_snippets = self._build_region_context_snippets(
                page_texts=page.get("texts", []),
                region_top=region_top,
                direction="before",
            )
            after_snippets = (
                [caption["text"]]
                if caption is not None
                else self._build_region_context_snippets(
                    page_texts=page.get("texts", []),
                    region_top=region_top,
                    direction="after",
                )
            )
            specs.append(
                {
                    "caption_text": caption["text"] if caption is not None else "",
                    "caption_bbox": caption["bbox"] if caption is not None else {},
                    "figure_bbox": layout_bbox,
                    "before_snippets": before_snippets,
                    "after_snippets": after_snippets,
                    "crop_box": crop_box,
                    "locator": "vlm_all",
                }
            )

        return self._deduplicate_region_specs(specs)

    def _resolve_crop_box(
        self,
        *,
        page: dict[str, Any],
        page_image: Image.Image,
        spec: dict[str, Any],
        scale_x: float,
        scale_y: float,
    ) -> tuple[tuple[int, int, int, int], dict[str, int], str]:
        heuristic_bbox = spec["figure_bbox"]
        heuristic_crop_box = self._layout_bbox_to_image_crop_box(
            bbox=heuristic_bbox,
            image_width=page_image.size[0],
            image_height=page_image.size[1],
            scale_x=scale_x,
            scale_y=scale_y,
        )
        caption_bbox = self._scale_bbox_to_image(
            bbox=spec["caption_bbox"],
            image_width=page_image.size[0],
            image_height=page_image.size[1],
            scale_x=scale_x,
            scale_y=scale_y,
        )
        heuristic_image = page_image.crop(heuristic_crop_box)
        heuristic_caption_bbox = self._shift_bbox(
            caption_bbox,
            dx=-heuristic_crop_box[0],
            dy=-heuristic_crop_box[1],
        )

        vlm_crop_box = self._locate_figure_crop_box_with_vlm(
            page_image=heuristic_image,
            spec=spec,
            caption_bbox=heuristic_caption_bbox,
        )
        if vlm_crop_box is not None:
            vlm_crop_box = self._offset_crop_box(
                vlm_crop_box,
                dx=heuristic_crop_box[0],
                dy=heuristic_crop_box[1],
            )
            vlm_crop_box = self._trim_crop_box_using_projection(
                page_image=page_image,
                crop_box=vlm_crop_box,
                preferred_center_x=(caption_bbox["left"] + caption_bbox["right"]) // 2,
            )
            layout_bbox = self._image_crop_box_to_layout_bbox(
                crop_box=vlm_crop_box,
                page=page,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            return vlm_crop_box, layout_bbox, "vlm"

        full_page_vlm_crop_box = self._locate_figure_crop_box_with_vlm(
            page_image=page_image,
            spec=spec,
            caption_bbox=caption_bbox,
        )
        if full_page_vlm_crop_box is not None:
            full_page_vlm_crop_box = self._trim_crop_box_using_projection(
                page_image=page_image,
                crop_box=full_page_vlm_crop_box,
                preferred_center_x=(caption_bbox["left"] + caption_bbox["right"]) // 2,
            )
            layout_bbox = self._image_crop_box_to_layout_bbox(
                crop_box=full_page_vlm_crop_box,
                page=page,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            return full_page_vlm_crop_box, layout_bbox, "vlm"

        return heuristic_crop_box, heuristic_bbox, "heuristic"

    def _trim_crop_box_using_projection(
        self,
        *,
        page_image: Image.Image,
        crop_box: tuple[int, int, int, int],
        preferred_center_x: int,
    ) -> tuple[int, int, int, int]:
        if np is None:
            logger.debug(
                "pdf_figure_extract_service numpy unavailable, skip projection trim | crop_box=%s",
                crop_box,
            )
            return crop_box

        cropped = page_image.crop(crop_box).convert("L")
        pixel_array = np.asarray(cropped)
        if pixel_array.size == 0:
            return crop_box

        ink_mask = pixel_array < 245
        if not ink_mask.any():
            return crop_box

        horizontal_segment = self._find_primary_horizontal_segment(
            density=ink_mask.mean(axis=0),
            crop_width=cropped.size[0],
            preferred_index=max(0, min(preferred_center_x - crop_box[0], cropped.size[0] - 1)),
        )
        left = crop_box[0] + horizontal_segment[0]
        right = crop_box[0] + horizontal_segment[1]
        if right - left < max(40, cropped.size[0] // 4):
            return crop_box
        return left, crop_box[1], right, crop_box[3]

    def _find_primary_horizontal_segment(
        self,
        *,
        density: np.ndarray,
        crop_width: int,
        preferred_index: int,
    ) -> tuple[int, int]:
        if density.size == 0:
            return 0, 0

        window = max(5, min(31, (density.size // 24) | 1))
        kernel = np.ones(window, dtype=float) / window
        smoothed = np.convolve(density, kernel, mode="same")
        valley_threshold = max(float(smoothed.max()) * 0.10, 0.004)
        min_gutter_span = max(24, crop_width // 18)
        min_segment_span = max(80, crop_width // 5)

        gutters: list[tuple[int, int]] = []
        gutter_start: int | None = None
        for index, value in enumerate(smoothed <= valley_threshold):
            if value and gutter_start is None:
                gutter_start = index
                continue
            if not value and gutter_start is not None:
                if index - gutter_start >= min_gutter_span:
                    gutters.append((gutter_start, index))
                gutter_start = None
        if gutter_start is not None and density.size - gutter_start >= min_gutter_span:
            gutters.append((gutter_start, density.size))

        if not gutters:
            return 0, density.size

        segments: list[tuple[int, int]] = []
        cursor = 0
        for gutter_start_index, gutter_end_index in gutters:
            if gutter_start_index - cursor >= min_segment_span:
                segments.append((cursor, gutter_start_index))
            cursor = gutter_end_index
        if density.size - cursor >= min_segment_span:
            segments.append((cursor, density.size))

        if len(segments) < 2:
            return 0, density.size

        preferred_index = max(0, min(preferred_index, density.size - 1))
        best_segment = min(
            segments,
            key=lambda segment: (
                abs(((segment[0] + segment[1]) // 2) - preferred_index),
                -(segment[1] - segment[0]),
            ),
        )
        margin = max(8, crop_width // 50)
        return (
            max(0, best_segment[0] - margin),
            min(density.size, best_segment[1] + margin),
        )

    @staticmethod
    def _offset_crop_box(
        crop_box: tuple[int, int, int, int],
        *,
        dx: int,
        dy: int,
    ) -> tuple[int, int, int, int]:
        return (
            crop_box[0] + dx,
            crop_box[1] + dy,
            crop_box[2] + dx,
            crop_box[3] + dy,
        )

    @staticmethod
    def _shift_bbox(bbox: dict[str, int], *, dx: int, dy: int) -> dict[str, int]:
        return {
            "left": bbox["left"] + dx,
            "top": bbox["top"] + dy,
            "right": bbox["right"] + dx,
            "bottom": bbox["bottom"] + dy,
        }

    def _build_figure_specs(self, page: dict[str, Any]) -> list[dict[str, Any]]:
        texts = page.get("texts", [])
        images = page.get("images", [])
        if not texts:
            return []

        captions = self._find_caption_groups(texts)
        figure_specs: list[dict[str, Any]] = []
        for caption in captions:
            column_bounds = self._infer_column_bounds(page, caption)
            figure_bbox = self._infer_figure_bbox(page, caption, column_bounds)
            if figure_bbox is None:
                continue
            if not self._looks_like_chart_or_figure_region(page, column_bounds, figure_bbox):
                continue
            if self._has_embedded_image_in_region(images, figure_bbox):
                # 避免与 pdfimages / <image> 提取的位图重复。
                continue

            figure_specs.append(
                {
                    "caption_text": caption["text"],
                    "caption_bbox": caption["bbox"],
                    "figure_bbox": figure_bbox,
                    "before_snippets": caption["before_snippets"],
                    "after_snippets": [caption["text"]],
                }
            )
        return figure_specs

    def _looks_like_chart_or_figure_region(
        self,
        page: dict[str, Any],
        column_bounds: dict[str, int],
        figure_bbox: dict[str, int],
    ) -> bool:
        region_texts = [
            item
            for item in page.get("texts", [])
            if int(item["top"]) >= figure_bbox["top"]
            and int(item["top"]) + int(item["height"]) <= figure_bbox["bottom"]
            and int(item["left"]) < column_bounds["right"]
            and int(item["left"]) + int(item["width"]) > column_bounds["left"]
        ]
        if not region_texts:
            return False

        column_width = max(column_bounds["right"] - column_bounds["left"], 1)
        wide_line_count = sum(
            1
            for item in region_texts
            if int(item["width"]) / column_width >= 0.65
        )
        return wide_line_count <= 3

    def _find_caption_groups(self, texts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        captions: list[dict[str, Any]] = []
        index = 0
        total = len(texts)
        while index < total:
            text_item = texts[index]
            content = str(text_item.get("text", "")).strip()
            if not self._CAPTION_PATTERN.match(content):
                index += 1
                continue

            grouped = [text_item]
            next_index = index + 1
            while next_index < total:
                candidate = texts[next_index]
                vertical_gap = int(candidate["top"]) - (
                    int(grouped[-1]["top"]) + int(grouped[-1]["height"])
                )
                if vertical_gap > 10:
                    break
                if abs(int(candidate["left"]) - int(text_item["left"])) > 80:
                    break
                grouped.append(candidate)
                next_index += 1

            bbox = {
                "left": min(int(item["left"]) for item in grouped),
                "top": min(int(item["top"]) for item in grouped),
                "right": max(int(item["left"]) + int(item["width"]) for item in grouped),
                "bottom": max(int(item["top"]) + int(item["height"]) for item in grouped),
            }
            before_snippets = [
                str(item["text"]).strip()
                for item in texts[max(0, index - 2):index]
                if str(item.get("text", "")).strip()
            ]
            captions.append(
                {
                    "text": " ".join(str(item["text"]).strip() for item in grouped).strip(),
                    "bbox": bbox,
                    "before_snippets": before_snippets[-2:],
                }
            )
            index = next_index

        return captions

    def _locate_figure_crop_box_with_vlm(
        self,
        *,
        page_image: Image.Image,
        spec: dict[str, Any],
        caption_bbox: dict[str, int],
    ) -> tuple[int, int, int, int] | None:
        hint = spec.get("caption_text", "") or "main figure"
        regions = self._request_vlm_regions(
            page_image=page_image,
            hint=str(hint),
            max_regions=3,
            detect_mode="primary",
        )
        if not regions:
            return None

        selected_region = self._select_best_region_for_caption(
            regions=regions,
            caption_bbox=caption_bbox,
            image_width=page_image.size[0],
            image_height=page_image.size[1],
        )
        if selected_region is None:
            return None

        cropped_box = self._clip_region_above_caption(
            bbox=selected_region,
            caption_bbox=caption_bbox,
            image_width=page_image.size[0],
            image_height=page_image.size[1],
        )
        if cropped_box is None:
            return None
        return cropped_box

    @staticmethod
    def _encode_png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _request_vlm_regions(
        self,
        *,
        page_image: Image.Image,
        hint: str,
        max_regions: int,
        detect_mode: str,
    ) -> list[dict[str, Any]]:
        if not self._figure_locate_url:
            return []

        image_bytes = self._encode_png(page_image)
        try:
            response = self._request_post(
                self._figure_locate_url,
                data={
                    "hint": str(hint),
                    "max_regions": str(max_regions),
                    "detect_mode": detect_mode,
                },
                files={
                    "file": ("page.png", image_bytes, "image/png"),
                },
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
        except BaseException as exc:
            logger.warning(
                "pdf_figure_extract_service figure locate request failed | url=%s hint=%s detect_mode=%s error=%s",
                self._figure_locate_url,
                hint,
                detect_mode,
                exc,
                exc_info=True,
            )
            return []

        regions = payload.get("regions", [])
        return regions if isinstance(regions, list) else []

    def _select_best_region_for_caption(
        self,
        *,
        regions: list[dict[str, Any]],
        caption_bbox: dict[str, Any],
        image_width: int,
        image_height: int,
    ) -> tuple[int, int, int, int] | None:
        if not regions:
            return None

        caption_left = int(caption_bbox.get("left", 0))
        caption_right = int(caption_bbox.get("right", caption_left))
        caption_top = int(caption_bbox.get("top", image_height))
        caption_center_x = (caption_left + caption_right) / 2 if caption_right > caption_left else image_width / 2

        best_score: tuple[float, float, float] | None = None
        best_bbox: tuple[int, int, int, int] | None = None

        for region in regions:
            bbox = region.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            try:
                left, top, right, bottom = [int(round(float(value))) for value in bbox]
            except (TypeError, ValueError):
                continue

            left = max(0, min(left, image_width))
            top = max(0, min(top, image_height))
            right = max(0, min(right, image_width))
            bottom = max(0, min(bottom, image_height))
            if right <= left or bottom <= top:
                continue

            area = (right - left) * (bottom - top)
            if area < 2000:
                continue

            region_center_x = (left + right) / 2
            horizontal_distance = abs(region_center_x - caption_center_x)
            above_caption_bonus = 1.0 if bottom <= caption_top + 24 else 0.0
            confidence = float(region.get("confidence", 0.0) or 0.0)
            score = (
                above_caption_bonus,
                confidence,
                area - horizontal_distance * 12,
            )
            if best_score is None or score > best_score:
                best_score = score
                best_bbox = (left, top, right, bottom)

        return best_bbox

    def _clip_region_above_caption(
        self,
        *,
        bbox: tuple[int, int, int, int],
        caption_bbox: dict[str, Any],
        image_width: int,
        image_height: int,
    ) -> tuple[int, int, int, int] | None:
        left, top, right, bottom = bbox
        caption_top = int(caption_bbox.get("top", image_height))
        if caption_top > 0:
            bottom = min(bottom, max(caption_top - 6, top))
        left = max(0, min(left, image_width))
        top = max(0, min(top, image_height))
        right = max(0, min(right, image_width))
        bottom = max(0, min(bottom, image_height))
        if right - left < 40 or bottom - top < 40:
            return None
        return left, top, right, bottom

    def _image_crop_box_to_layout_bbox(
        self,
        *,
        crop_box: tuple[int, int, int, int],
        page: dict[str, Any],
        scale_x: float,
        scale_y: float,
    ) -> dict[str, int]:
        page_width = int(page.get("width", 0))
        page_height = int(page.get("height", 0))
        left, top, right, bottom = crop_box
        return {
            "left": max(0, min(page_width, int(round(left / max(scale_x, 1e-6))))),
            "top": max(0, min(page_height, int(round(top / max(scale_y, 1e-6))))),
            "right": max(0, min(page_width, int(round(right / max(scale_x, 1e-6))))),
            "bottom": max(0, min(page_height, int(round(bottom / max(scale_y, 1e-6))))),
        }

    def _build_region_context_snippets(
        self,
        *,
        page_texts: list[dict[str, Any]],
        region_top: int,
        direction: str,
    ) -> list[str]:
        if not page_texts:
            return []

        before_items = [
            item
            for item in page_texts
            if str(item.get("text", "")).strip() and int(item.get("top", 0)) <= region_top
        ]
        after_items = [
            item
            for item in page_texts
            if str(item.get("text", "")).strip() and int(item.get("top", 0)) > region_top
        ]

        if direction == "before":
            selected = [str(item["text"]).strip() for item in before_items[-3:]]
            candidates = [
                " ".join(selected[-2:]).strip(),
                selected[-1].strip() if selected else "",
            ]
        else:
            selected = [str(item["text"]).strip() for item in after_items[:3]]
            candidates = [
                " ".join(selected[:2]).strip(),
                selected[0].strip() if selected else "",
            ]

        snippets: list[str] = []
        for candidate in candidates:
            cleaned = " ".join(candidate.split()).strip()
            if cleaned and cleaned not in snippets and len(cleaned) >= 8:
                snippets.append(cleaned)
        return snippets

    def _find_caption_for_region(
        self,
        captions: list[dict[str, Any]],
        figure_bbox: dict[str, int],
    ) -> dict[str, Any] | None:
        best_caption: dict[str, Any] | None = None
        best_score: tuple[int, int] | None = None
        for caption in captions:
            caption_bbox = caption["bbox"]
            vertical_gap = int(caption_bbox["top"]) - int(figure_bbox["bottom"])
            if vertical_gap < -16 or vertical_gap > 180:
                continue

            horizontal_overlap = min(int(figure_bbox["right"]), int(caption_bbox["right"])) - max(
                int(figure_bbox["left"]),
                int(caption_bbox["left"]),
            )
            if horizontal_overlap <= 0:
                continue

            score = (vertical_gap, -horizontal_overlap)
            if best_score is None or score < best_score:
                best_score = score
                best_caption = caption
        return best_caption

    def _looks_like_full_page_region(
        self,
        figure_bbox: dict[str, int],
        page: dict[str, Any],
    ) -> bool:
        page_width = max(int(page.get("width", 0)), 1)
        page_height = max(int(page.get("height", 0)), 1)
        width_ratio = (int(figure_bbox["right"]) - int(figure_bbox["left"])) / page_width
        height_ratio = (int(figure_bbox["bottom"]) - int(figure_bbox["top"])) / page_height
        return width_ratio >= 0.94 and height_ratio >= 0.94

    def _deduplicate_region_specs(
        self,
        specs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        for spec in sorted(
            specs,
            key=lambda item: (
                0 if str(item.get("caption_text", "")).strip() else 1,
                -self._bbox_area(item["figure_bbox"]),
                int(item["figure_bbox"]["top"]),
                int(item["figure_bbox"]["left"]),
            ),
        ):
            bbox = spec["figure_bbox"]
            has_caption = bool(str(spec.get("caption_text", "")).strip())
            is_duplicate = False
            for existing in deduped:
                existing_has_caption = bool(str(existing.get("caption_text", "")).strip())
                overlap_ratio = self._bbox_overlap_ratio(bbox, existing["figure_bbox"])
                if existing_has_caption and not has_caption and overlap_ratio >= 0.35:
                    is_duplicate = True
                    break
                if overlap_ratio >= 0.55:
                    is_duplicate = True
                    break
            if not is_duplicate:
                deduped.append(spec)
        return deduped

    def _bbox_overlap_ratio(
        self,
        first_bbox: dict[str, int],
        second_bbox: dict[str, int],
    ) -> float:
        overlap_width = min(int(first_bbox["right"]), int(second_bbox["right"])) - max(
            int(first_bbox["left"]),
            int(second_bbox["left"]),
        )
        overlap_height = min(int(first_bbox["bottom"]), int(second_bbox["bottom"])) - max(
            int(first_bbox["top"]),
            int(second_bbox["top"]),
        )
        if overlap_width <= 0 or overlap_height <= 0:
            return 0.0
        overlap_area = overlap_width * overlap_height
        return overlap_area / min(self._bbox_area(first_bbox), self._bbox_area(second_bbox))

    @staticmethod
    def _bbox_area(bbox: dict[str, int]) -> int:
        return max(1, int(bbox["right"]) - int(bbox["left"])) * max(
            1,
            int(bbox["bottom"]) - int(bbox["top"]),
        )

    def _layout_bbox_to_image_crop_box(
        self,
        *,
        bbox: dict[str, int],
        image_width: int,
        image_height: int,
        scale_x: float,
        scale_y: float,
    ) -> tuple[int, int, int, int]:
        return (
            max(0, int(round(bbox["left"] * scale_x))),
            max(0, int(round(bbox["top"] * scale_y))),
            min(image_width, int(round(bbox["right"] * scale_x))),
            min(image_height, int(round(bbox["bottom"] * scale_y))),
        )

    def _scale_bbox_to_image(
        self,
        *,
        bbox: dict[str, int],
        image_width: int,
        image_height: int,
        scale_x: float,
        scale_y: float,
    ) -> dict[str, int]:
        left, top, right, bottom = self._layout_bbox_to_image_crop_box(
            bbox=bbox,
            image_width=image_width,
            image_height=image_height,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        return {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
        }

    def _infer_column_bounds(
        self,
        page: dict[str, Any],
        caption: dict[str, Any],
    ) -> dict[str, int]:
        texts = page.get("texts", [])
        caption_left = int(caption["bbox"]["left"])
        candidate_blocks = [
            item
            for item in texts
            if abs(int(item["left"]) - caption_left) <= 120
        ]
        if not candidate_blocks:
            candidate_blocks = texts

        left = min(int(item["left"]) for item in candidate_blocks)
        right = max(int(item["left"]) + int(item["width"]) for item in candidate_blocks)
        return {"left": left, "right": right}

    def _infer_figure_bbox(
        self,
        page: dict[str, Any],
        caption: dict[str, Any],
        column_bounds: dict[str, int],
    ) -> dict[str, int] | None:
        page_width = int(page.get("width", 0))
        page_height = int(page.get("height", 0))
        caption_left = int(caption["bbox"]["left"])
        caption_right = int(caption["bbox"]["right"])
        texts = [
            item
            for item in page.get("texts", [])
            if int(item["left"]) < column_bounds["right"]
            and int(item["left"]) + int(item["width"]) > column_bounds["left"]
        ]
        if not texts:
            return None

        caption_top = int(caption["bbox"]["top"])
        texts_above_caption = [
            item
            for item in texts
            if int(item["top"]) + int(item["height"]) <= caption_top
        ]
        if not texts_above_caption:
            return None

        texts_above_caption.sort(key=lambda item: (int(item["top"]), int(item["left"])))
        top = int(texts_above_caption[0]["top"])
        for prev, current in zip(texts_above_caption, texts_above_caption[1:]):
            prev_bottom = int(prev["top"]) + int(prev["height"])
            gap = int(current["top"]) - prev_bottom
            if gap >= 18 and caption_top - int(current["top"]) >= 120:
                top = int(current["top"])

        bottom = caption_top - 8
        left = max(0, max(column_bounds["left"] - 12, caption_left - 90))
        right = min(page_width, max(column_bounds["right"] + 12, caption_right + 12))
        top = max(0, top - 8)
        bottom = min(page_height, bottom)

        if bottom - top < 80 or right - left < 120:
            return None
        return {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
        }

    def _has_embedded_image_in_region(
        self,
        images: list[dict[str, Any]],
        figure_bbox: dict[str, int],
    ) -> bool:
        for image in images:
            image_left = int(image["left"])
            image_top = int(image["top"])
            image_right = image_left + int(image["width"])
            image_bottom = image_top + int(image["height"])
            horizontal_overlap = min(figure_bbox["right"], image_right) - max(
                figure_bbox["left"], image_left
            )
            vertical_overlap = min(figure_bbox["bottom"], image_bottom) - max(
                figure_bbox["top"], image_top
            )
            if horizontal_overlap > 0 and vertical_overlap > 0:
                return True
        return False

    def _render_pdf_pages(
        self,
        *,
        file_path: Path,
        render_prefix: Path,
        password: str | None,
        dpi: int,
    ) -> dict[int, Path]:
        command = ["pdftocairo", "-png", "-r", str(dpi)]
        command.extend(self._build_password_args(password))
        command.extend([str(file_path), str(render_prefix)])
        self._run_command(command, "渲染 PDF 页面")

        rendered_pages: dict[int, Path] = {}
        for path in render_prefix.parent.glob(f"{render_prefix.name}-*.png"):
            match = re.search(r"-(\d+)\.png$", path.name)
            if match:
                rendered_pages[int(match.group(1))] = path
        return rendered_pages

    def _prepare_output_dir(self, output_dir: Path) -> None:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    def _run_command(self, command: list[str], action: str) -> subprocess.CompletedProcess[str]:
        logger.info("pdf_figure_extract_service command started | action=%s command=%s", action, command)
        result = self._command_runner(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            raise RuntimeError(
                f"{action}失败: {stderr or stdout or f'exit code {result.returncode}'}"
            )
        return result

    @staticmethod
    def _build_password_args(password: str | None) -> list[str]:
        if not password:
            return []
        return ["-opw", password, "-upw", password]
