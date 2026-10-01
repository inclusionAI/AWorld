"""Targeted structured repair for scorer-facing table and chart blocks."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Callable, Protocol

from ..media_transcription.openai_compatible_backend import (
    OpenAICompatibleMediaTranscriptionBackend,
)

MAX_REPAIR_BLOCKS = 32
MAX_REPAIR_ATTEMPTS = 2
MAX_COLUMNS = 50
MAX_ROWS = 500
MAX_CELL_CHARS = 2048
MAX_MODEL_OUTPUT_CHARS = 2 * 1024 * 1024

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_NUMERIC = re.compile(
    r"^~?\s*[$€£¥]?\s*[-+]?(?:\d[\d, ]*|\d*\.\d+)"
    r"(?:\.\d+)?\s*(?:%|[kKmMbBtT]|million|billion|trillion)?$",
    re.IGNORECASE,
)
_NUMERIC_RANGE = re.compile(
    r"^\s*~?\s*[$€£¥]?\s*[-+]?\d[\d,. ]*\s*"
    r"(?:[-–—]|\bto\b)\s*[-+]?\d[\d,. ]*\s*%?\s*$",
    re.IGNORECASE,
)
_NARRATIVE_ESTIMATE = re.compile(
    r"\b(?:about|approx(?:\.|imately)?|around|between|estimated|roughly)\b",
    re.IGNORECASE,
)
_SEMANTIC_HEADER = re.compile(r"[^\W\d_]", re.UNICODE)

_TABLE_PROMPT = """Extract the visible table into strict JSON only.
Return exactly {"columns":["..."],"rows":[["...", "..."]]}.
Use at least two columns and one row. Preserve every visible header and cell.
Repeat merged header values where needed to form a rectangular grid. Do not
return Markdown, HTML, prose, commentary, or invented values."""

_CHART_PROMPT = """Extract the visible chart semantics into one flat rectangular
two-dimensional table in strict JSON only. Return exactly
{"columns":["..."],"rows":[["...", "..."]]}. Use the actual visible axis,
legend, category, or series labels as column headers (for example Year,
Revenue, Profit); do not replace them with a fixed generic schema. Preserve the
labels associated with each numeric value in the same row or column. A
single-series or single-label chart may use two columns. Every row must contain
at least one numeric value. Transcribe printed values exactly. When a value is
not printed, a bounded visual read from a clear axis scale and unambiguous bar
or point position is allowed; prefix that value with ~. Never estimate without
a visible axis basis, invent values, emit ranges, or return prose/commentary."""


class BlockRepairError(ValueError):
    """A stable failure while validating or applying one block repair."""


class BlockRepairBackend(Protocol):
    async def transcribe(
        self,
        file_path: Path,
        *,
        media_type: str,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class StructuredTable:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    @classmethod
    def from_model_output(cls, content: str, *, require_numeric: bool) -> "StructuredTable":
        if not isinstance(content, str) or not content.strip():
            raise BlockRepairError("filex_block_repair_empty_output")
        if len(content) > MAX_MODEL_OUTPUT_CHARS:
            raise BlockRepairError("filex_block_repair_output_too_large")
        value = content.strip()
        if value.startswith("```"):
            value = _FENCE.sub("", value).strip()
        try:
            payload = json.loads(value)
        except (json.JSONDecodeError, ValueError, RecursionError):
            raise BlockRepairError("filex_block_repair_invalid_json") from None
        if not isinstance(payload, dict) or set(payload) != {"columns", "rows"}:
            raise BlockRepairError("filex_block_repair_schema_invalid")
        columns = payload["columns"]
        rows = payload["rows"]
        if (
            not isinstance(columns, list)
            or not 2 <= len(columns) <= MAX_COLUMNS
            or not isinstance(rows, list)
            or not 1 <= len(rows) <= MAX_ROWS
        ):
            raise BlockRepairError("filex_block_repair_shape_invalid")
        normalized_columns = tuple(_cell_text(cell) for cell in columns)
        if any(not column for column in normalized_columns):
            raise BlockRepairError("filex_block_repair_header_invalid")
        normalized_rows: list[tuple[str, ...]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(normalized_columns):
                raise BlockRepairError("filex_block_repair_row_invalid")
            normalized_rows.append(tuple(_cell_text(cell) for cell in row))
        if require_numeric:
            _validate_chart_table(normalized_columns, normalized_rows)
        return cls(columns=normalized_columns, rows=tuple(normalized_rows))

    def to_html(self) -> str:
        header = "".join(f"<th>{escape(cell)}</th>" for cell in self.columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
            for row in self.rows
        )
        return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise BlockRepairError("filex_block_repair_cell_invalid")
    if isinstance(value, float) and not math.isfinite(value):
        raise BlockRepairError("filex_block_repair_cell_invalid")
    text = str(value).strip()
    if len(text) > MAX_CELL_CHARS:
        raise BlockRepairError("filex_block_repair_cell_too_large")
    return text


def _validate_chart_table(
    columns: tuple[str, ...], rows: list[tuple[str, ...]]
) -> None:
    normalized = tuple(
        re.sub(r"\s+", " ", value).strip().casefold() for value in columns
    )
    if len(set(normalized)) != len(normalized):
        raise BlockRepairError("filex_chart_repair_header_ambiguous")
    header_has_label = any(
        _SEMANTIC_HEADER.search(column) and _NUMERIC.fullmatch(column) is None
        for column in normalized
    )
    row_has_label = any(
        cell.strip() and _NUMERIC.fullmatch(cell.strip()) is None
        for row in rows
        for cell in row
    )
    if not header_has_label and not row_has_label:
        raise BlockRepairError("filex_chart_repair_labels_missing")
    for row in rows:
        numeric_count = 0
        label_count = 0
        for cell in row:
            value = re.sub(r"\s+", " ", cell).strip()
            if _NUMERIC_RANGE.fullmatch(value):
                raise BlockRepairError("filex_chart_repair_range_invalid")
            if _NARRATIVE_ESTIMATE.search(value):
                raise BlockRepairError("filex_chart_repair_narrative_value_invalid")
            if _NUMERIC.fullmatch(value):
                numeric_count += 1
            elif value:
                label_count += 1
        if numeric_count == 0:
            raise BlockRepairError("filex_chart_repair_numeric_value_missing")
        if label_count == 0 and numeric_count < 2:
            raise BlockRepairError("filex_chart_repair_row_labels_missing")


def _gateway_options(*, prompt: str, kind: str) -> dict[str, Any]:
    options: dict[str, Any] = {
        "prompt": prompt,
        "timeout_seconds": int(os.getenv("FILEX_BLOCK_REPAIR_TIMEOUT_SECONDS", "180")),
        "max_tokens": int(os.getenv("FILEX_BLOCK_REPAIR_MAX_TOKENS", "4096")),
        "temperature": 0,
    }
    mapping = {
        "FILEX_PADDLE_OCR_VL_REC_SERVER_URL": "base_url",
        "GATEWAY_VLLM_BASE_URL": "base_url",
        "FILEX_PADDLE_OCR_VL_REC_API_KEY": "api_key",
        "GATEWAY_VLLM_API_KEY": "api_key",
        "FILEX_PADDLE_OCR_VL_REC_API_MODEL_NAME": "model",
        "GATEWAY_VLLM_HTTP_MODEL_NAME": "model",
        "GATEWAY_VLLM_MODEL_NAME": "model",
    }
    for env_key, option_key in mapping.items():
        value = os.getenv(env_key, "").strip()
        if value and option_key not in options:
            options[option_key] = value
    specialized_model = os.getenv(
        "FILEX_TABLE_REPAIR_MODEL" if kind == "tables" else "FILEX_CHART_REPAIR_MODEL",
        "",
    ).strip()
    if specialized_model:
        options["model"] = specialized_model
    options["extra_body"] = {
        "enable_maya_new_inference_protocol": True,
        "enable_sec_check": True,
    }
    return options


def _render_crop(
    source_path: Path,
    *,
    page_number: int,
    bbox: dict[str, Any],
    page_width: float,
    page_height: float,
    output_path: Path,
    padding_ratio: float = 0.20,
) -> Path:
    if source_path.suffix.lower() == ".pdf":
        from pdf2image import convert_from_path

        images = convert_from_path(
            str(source_path), dpi=220, first_page=page_number, last_page=page_number
        )
        if not images:
            raise BlockRepairError("filex_block_repair_page_render_failed")
        image = images[0].convert("RGB")
    else:
        from PIL import Image

        if page_number != 1:
            raise BlockRepairError("filex_block_repair_page_invalid")
        image = Image.open(source_path).convert("RGB")
    if page_width <= 0 or page_height <= 0:
        raise BlockRepairError("filex_block_repair_page_geometry_invalid")
    if not math.isfinite(padding_ratio) or not 0 <= padding_ratio <= 0.5:
        raise BlockRepairError("filex_block_repair_padding_invalid")
    try:
        x = float(bbox["x"]) * image.width / page_width
        y = float(bbox["y"]) * image.height / page_height
        w = float(bbox["w"]) * image.width / page_width
        h = float(bbox["h"]) * image.height / page_height
    except (KeyError, TypeError, ValueError, OverflowError):
        raise BlockRepairError("filex_block_repair_bbox_invalid") from None
    # Axis labels and legends commonly sit immediately outside the detector's
    # chart body. Keep bounded surrounding context instead of cropping to the
    # plotted rectangle plus only a couple of pixels.
    padding_x = max(w * padding_ratio, 12.0)
    padding_y = max(h * padding_ratio, 12.0)
    crop = (
        max(0, int(x - padding_x)),
        max(0, int(y - padding_y)),
        min(image.width, int(math.ceil(x + w + padding_x))),
        min(image.height, int(math.ceil(y + h + padding_y))),
    )
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        raise BlockRepairError("filex_block_repair_bbox_invalid")
    image.crop(crop).save(output_path, format="PNG")
    return output_path


def _anchored_rewrite(
    content: str,
    candidates: list[str],
    replacement: str,
    *,
    scope: str,
) -> str:
    """Replace one uniquely anchored block without a global first-match fallback."""

    values = {value for value in candidates if isinstance(value, str) and value.strip()}
    for length in sorted({len(value) for value in values}, reverse=True):
        positions: set[tuple[int, int]] = set()
        for candidate in (value for value in values if len(value) == length):
            start = 0
            while True:
                index = content.find(candidate, start)
                if index < 0:
                    break
                positions.add((index, index + len(candidate)))
                start = index + 1
        if not positions:
            continue
        if len(positions) != 1:
            raise BlockRepairError(f"filex_block_repair_{scope}_anchor_ambiguous")
        start, end = next(iter(positions))
        return content[:start] + replacement + content[end:]
    raise BlockRepairError("filex_block_repair_anchor_missing")


def _anchored_insert(
    content: str,
    candidates: list[str],
    insertion: str,
    *,
    before: bool,
    scope: str,
) -> str:
    values = {
        value for value in candidates if isinstance(value, str) and value.strip()
    }
    for length in sorted({len(value) for value in values}, reverse=True):
        matches: list[tuple[int, int, str]] = []
        for candidate in (value for value in values if len(value) == length):
            start = 0
            while True:
                index = content.find(candidate, start)
                if index < 0:
                    break
                matches.append((index, index + len(candidate), candidate))
                start = index + 1
        unique = {(start, end) for start, end, _candidate in matches}
        if not unique:
            continue
        if len(unique) != 1:
            raise BlockRepairError(f"filex_block_repair_{scope}_anchor_ambiguous")
        start, end = next(iter(unique))
        anchor = content[start:end]
        replacement = (
            f"{insertion}\n\n{anchor}" if before else f"{anchor}\n\n{insertion}"
        )
        return content[:start] + replacement + content[end:]
    raise BlockRepairError("filex_block_repair_anchor_missing")


def _target_page(layout: dict[str, Any], issue: dict[str, Any]) -> dict[str, Any]:
    page_number = issue.get("page_number")
    pages = layout.get("layout_pages")
    matches = (
        [
            page
            for page in pages
            if isinstance(page, dict)
            and page.get("page_number", page.get("page")) == page_number
        ]
        if isinstance(pages, list)
        else []
    )
    if not matches:
        raise BlockRepairError("filex_block_repair_target_page_missing")
    if len(matches) != 1:
        raise BlockRepairError("filex_block_repair_target_page_ambiguous")
    return matches[0]


def _item_candidates(item: dict[str, Any]) -> list[str]:
    return [
        value
        for value in (item.get("md"), item.get("html"), item.get("value"))
        if isinstance(value, str)
    ]


def _bbox_key(item: dict[str, Any]) -> tuple[float, float]:
    bbox = item.get("bbox")
    if not isinstance(bbox, dict):
        return (math.inf, math.inf)
    try:
        key = (float(bbox["y"]), float(bbox["x"]))
    except (KeyError, TypeError, ValueError, OverflowError):
        return (math.inf, math.inf)
    return key if all(math.isfinite(value) for value in key) else (math.inf, math.inf)


def _normalized_chart_bbox(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BlockRepairError("filex_block_repair_bbox_invalid")
    try:
        numbers = {field: float(value[field]) for field in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError, OverflowError):
        raise BlockRepairError("filex_block_repair_bbox_invalid") from None
    if (
        not all(math.isfinite(number) for number in numbers.values())
        or numbers["x"] < 0
        or numbers["y"] < 0
        or numbers["w"] <= 0
        or numbers["h"] <= 0
    ):
        raise BlockRepairError("filex_block_repair_bbox_invalid")
    return {**numbers, "label": "picture"}


def _new_chart_item(
    page: dict[str, Any], issue: dict[str, Any]
) -> tuple[dict[str, Any], int, dict[str, Any], bool]:
    items = page.get("items")
    block_id = issue.get("block_id")
    if not isinstance(items, list):
        raise BlockRepairError("filex_block_repair_target_items_invalid")
    if not isinstance(block_id, str) or not block_id.strip():
        raise BlockRepairError("filex_block_repair_target_identity_missing")
    if any(
        isinstance(existing, dict) and existing.get("id") == block_id
        for existing in items
    ):
        raise BlockRepairError("filex_block_repair_target_identity_ambiguous")
    normalized_bbox = _normalized_chart_bbox(issue.get("bbox"))
    item = {
        "id": block_id,
        "type": "chart",
        "md": "",
        "html": "",
        "value": "",
        "bbox": normalized_bbox,
        "layout_segments": [dict(normalized_bbox)],
    }
    ordered = sorted(
        [*enumerate(items), (len(items), item)],
        key=lambda entry: (
            _bbox_key(entry[1]) if isinstance(entry[1], dict) else (math.inf, math.inf),
            entry[0],
        ),
    )
    items[:] = [existing for _original_index, existing in ordered]
    insertion_index = next(
        index for index, existing in enumerate(items) if existing is item
    )
    for index, existing in enumerate(items):
        if isinstance(existing, dict):
            existing["reading_order"] = index
    neighbor_index = (
        insertion_index + 1
        if insertion_index + 1 < len(items)
        else insertion_index - 1
    )
    if neighbor_index < 0 or not isinstance(items[neighbor_index], dict):
        raise BlockRepairError("filex_block_repair_anchor_missing")
    issue["item_index"] = insertion_index
    return item, insertion_index, items[neighbor_index], neighbor_index > insertion_index


def _target_item(layout: dict[str, Any], issue: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    page = _target_page(layout, issue)
    item_index = issue.get("item_index")
    items = page.get("items")
    block_id = issue.get("block_id")
    if (
        isinstance(items, list)
        and isinstance(item_index, int)
        and not isinstance(item_index, bool)
        and 0 <= item_index < len(items)
        and isinstance(items[item_index], dict)
    ):
        if not isinstance(block_id, str) or not block_id.strip():
            raise BlockRepairError("filex_block_repair_target_identity_missing")
        if items[item_index].get("id") != block_id:
            raise BlockRepairError("filex_block_repair_target_identity_mismatch")
        if (
            sum(
                1
                for candidate in items
                if isinstance(candidate, dict) and candidate.get("id") == block_id
            )
            != 1
        ):
            raise BlockRepairError("filex_block_repair_target_identity_ambiguous")
        return page, items[item_index]
    if item_index is None and issue.get("reason") == "filex_chart_content_unusable":
        item, _index, _neighbor, _before = _new_chart_item(page, issue)
        return page, item
    raise BlockRepairError("filex_block_repair_target_missing")


def _matching_parsed_page(
    layout: dict[str, Any], page_number: int
) -> dict[str, Any] | None:
    pages = layout.get("pages")
    if not isinstance(pages, list):
        return None
    matches = [
        page
        for page in pages
        if isinstance(page, dict) and page.get("page_index") == page_number - 1
    ]
    if len(matches) > 1:
        raise BlockRepairError("filex_block_repair_parsed_page_ambiguous")
    return matches[0] if matches else None


def apply_structured_repair(
    *,
    document: str,
    layout: dict[str, Any],
    issue: dict[str, Any],
    table: StructuredTable,
) -> tuple[str, dict[str, Any]]:
    updated_layout = copy.deepcopy(layout)
    updated_issue = dict(issue)
    page = _target_page(updated_layout, updated_issue)
    item_index = updated_issue.get("item_index")
    created = (
        item_index is None
        and updated_issue.get("reason") == "filex_chart_content_unusable"
    )
    neighbor: dict[str, Any] | None = None
    insert_before = False
    if created:
        item, item_index, neighbor, insert_before = _new_chart_item(page, updated_issue)
    else:
        _page, item = _target_item(updated_layout, updated_issue)
    html = table.to_html()
    old_candidates = _item_candidates(item)
    anchor_candidates = _item_candidates(neighbor) if neighbor is not None else old_candidates
    rewrite = _anchored_insert if created else _anchored_rewrite
    if created:
        document = rewrite(
            document, anchor_candidates, html, before=insert_before, scope="document"
        )
    else:
        document = rewrite(document, anchor_candidates, html, scope="document")
    page_markdown = page.get("md")
    if not isinstance(page_markdown, str):
        raise BlockRepairError("filex_block_repair_page_markdown_missing")
    if created:
        page["md"] = rewrite(
            page_markdown, anchor_candidates, html, before=insert_before, scope="page"
        )
    else:
        page["md"] = rewrite(page_markdown, anchor_candidates, html, scope="page")
    page_number = page.get("page_number", page.get("page"))
    if not isinstance(page_number, int) or isinstance(page_number, bool):
        raise BlockRepairError("filex_block_repair_target_page_invalid")
    parsed_page = _matching_parsed_page(updated_layout, page_number)
    if parsed_page is not None:
        parsed_markdown = parsed_page.get("markdown")
        if not isinstance(parsed_markdown, str):
            raise BlockRepairError("filex_block_repair_parsed_markdown_missing")
        if created:
            parsed_page["markdown"] = rewrite(
                parsed_markdown,
                anchor_candidates,
                html,
                before=insert_before,
                scope="parsed_page",
            )
        else:
            parsed_page["markdown"] = rewrite(
                parsed_markdown, anchor_candidates, html, scope="parsed_page"
            )
    item["md"] = html
    item["html"] = html
    item["value"] = html
    updated_layout["markdown"] = document
    issue["item_index"] = item_index
    return document, updated_layout


async def repair_parse_output(
    *,
    source_path: Path,
    document: str,
    layout: dict[str, Any],
    quality_report: dict[str, Any],
    backend: BlockRepairBackend | None = None,
    crop_renderer: Callable[..., Path] = _render_crop,
    max_blocks: int = MAX_REPAIR_BLOCKS,
) -> dict[str, Any]:
    backend = backend or OpenAICompatibleMediaTranscriptionBackend()
    issues: list[tuple[str, dict[str, Any]]] = []
    for kind in ("tables", "charts"):
        section = quality_report.get(kind)
        if not isinstance(section, dict) or not isinstance(section.get("issues"), list):
            continue
        issues.extend(
            (kind, issue) for issue in section["issues"] if isinstance(issue, dict)
        )
    if not isinstance(max_blocks, int) or isinstance(max_blocks, bool) or not 1 <= max_blocks <= MAX_REPAIR_BLOCKS:
        raise BlockRepairError("filex_block_repair_limit_invalid")
    repaired: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="filex-block-repair-") as temporary:
        temporary_root = Path(temporary)
        for repair_index, (kind, issue) in enumerate(issues[:max_blocks]):
            try:
                page_number = int(issue["page_number"])
                bbox = issue["bbox"]
                page = _target_page(layout, issue)
                crop_path = crop_renderer(
                    source_path,
                    page_number=page_number,
                    bbox=bbox,
                    page_width=float(page["width"]),
                    page_height=float(page["height"]),
                    output_path=temporary_root / f"block-{repair_index}.png",
                    padding_ratio=0.20 if kind == "charts" else 0.08,
                )
                prompt = _TABLE_PROMPT if kind == "tables" else _CHART_PROMPT
                table = None
                last_reason = "filex_block_repair_failed"
                for attempt in range(MAX_REPAIR_ATTEMPTS):
                    attempt_prompt = prompt
                    if attempt:
                        attempt_prompt += (
                            "\nCORRECTION: the prior response violated the JSON or rectangular "
                            "table contract. Read the image again and return only valid JSON."
                        )
                    try:
                        response = await backend.transcribe(
                            crop_path,
                            media_type="image",
                            file_type="png",
                            source_file_name=crop_path.name,
                            options=_gateway_options(prompt=attempt_prompt, kind=kind),
                        )
                        table = StructuredTable.from_model_output(
                            str(getattr(response, "text", "") or ""),
                            require_numeric=kind == "charts",
                        )
                        break
                    except BlockRepairError as exc:
                        last_reason = str(exc)
                    except Exception:
                        last_reason = "filex_block_repair_backend_failed"
                if table is None:
                    raise BlockRepairError(last_reason)
                working_issue = dict(issue)
                document, layout = apply_structured_repair(
                    document=document,
                    layout=layout,
                    issue=working_issue,
                    table=table,
                )
                repaired.append(
                    {
                        "kind": kind,
                        "page_number": page_number,
                        "item_index": working_issue.get("item_index"),
                        "block_id": issue.get("block_id"),
                    }
                )
            except (BlockRepairError, KeyError, OSError, ValueError) as exc:
                failures.append(
                    {
                        "kind": kind,
                        "page_number": issue.get("page_number"),
                        "item_index": issue.get("item_index"),
                        "block_id": issue.get("block_id"),
                        "reason": str(exc)[:128] or type(exc).__name__,
                    }
                )
    document_issues = quality_report.get("document")
    if isinstance(document_issues, dict) and document_issues.get("issues"):
        failures.append(
            {
                "kind": "document",
                "page_number": None,
                "item_index": None,
                "block_id": None,
                "reason": "filex_block_repair_document_anchor_required",
            }
        )
    return {
        "document": document,
        "layout": layout,
        "repaired": repaired,
        "failures": failures,
        "attempted": min(len(issues), max_blocks),
        "remaining": max(0, len(issues) - max_blocks),
    }


async def _main() -> int:
    try:
        request = json.load(sys.stdin)
        result = await repair_parse_output(
            source_path=Path(request["source_path"]),
            document=str(request["document"]),
            layout=request["layout"],
            quality_report=request["quality_report"],
            max_blocks=int(request.get("max_blocks") or MAX_REPAIR_BLOCKS),
        )
        json.dump({"success": True, **result}, sys.stdout, ensure_ascii=False)
        return 0
    except (BlockRepairError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, BlockRepairError) else "filex_block_repair_failed"
        json.dump(
            {
                "success": False,
                "reason": reason[:128] or "filex_block_repair_failed",
            },
            sys.stdout,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
