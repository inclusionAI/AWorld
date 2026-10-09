"""Targeted structured repair for public table and chart blocks."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass, replace
from html import escape, unescape
from pathlib import Path
from typing import Any, Callable, Protocol

from ..parse_output_export import normalize_structured_tables
from ..media_transcription.openai_compatible_backend import (
    OpenAICompatibleMediaTranscriptionBackend,
)

MAX_REPAIR_BLOCKS = 32
MAX_REPAIR_ATTEMPTS = 2
MAX_COLUMNS = 50
MAX_ROWS = 500
MAX_CELL_CHARS = 2048
MAX_MODEL_OUTPUT_CHARS = 2 * 1024 * 1024
MAX_MODEL_BOILERPLATE_CHARS = 4096
MAX_REPAIR_OUTPUT_TOKENS = 16_384

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_NUMERIC = re.compile(
    r"^[~≈]?\s*[$€£¥]?\s*[-+]?(?:\d[\d, ]*|\d*\.\d+)"
    r"(?:\.\d+)?\s*(?:%|[kKmMbBtT]|million|billion|trillion)?"
    r"(?:\*{1,3}|[⁰¹²³⁴⁵⁶⁷⁸⁹]+|[†‡])?$",
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
_MARKED_CURRENCY_VALUE = re.compile(r"^[~≈]\s*[$€£¥]")
_MISSING_CHART_VALUE = re.compile(r"^(?:n/?a|—|–|-|…|\.\.)$", re.IGNORECASE)
_SEMANTIC_HEADER = re.compile(r"[^\W\d_]", re.UNICODE)
_HTML_TABLE_BLOCK = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)

_TABLE_PROMPT = """Extract the visible table into strict JSON only.
Return {"caption":"visible caption or empty string","notes":["exact visible
footnote/source/unit note"],"columns":[...],"rows":[...]} with at least two
logical columns and one body row. `columns` is the first physical header row;
the sum of its colspan values defines the logical grid width. Each entry in
`rows` is one later physical row and, after active rowspans are accounted for,
must fill exactly that width. A simple cell may be a string. A merged/header
cell must be {"text":"...","rowspan":1,"colspan":1,"header":true}. Mark every
visible top or row header with header=true. Preserve every visible cell and the
exact rowspan/colspan structure; do not flatten or repeat merged headers. For
example, a two-level three-column header may start with columns=[{"text":"Region",
"rowspan":2,"header":true},{"text":"Revenue","colspan":2,"header":true}]
and rows=[[{"text":"Q1","header":true},{"text":"Q2","header":true}],...].
Omit no visible caption. Do not return Markdown, prose, commentary, or invented
values."""

_CHART_PROMPT = """Extract the visible chart semantics into strict JSON only.
Return {"caption":"exact visible title/caption or empty string","labels":[...],
"notes":["exact visible footnote/source/unit note"],"estimated":false,
"value_columns":[1],"columns":[...],"rows":[...]}. The
zero-based value_columns must identify numeric measure columns, never category,
date, year, or label columns. List every visible axis, legend, category, date,
and series label in labels and also place each label in the caption, a header,
or the associated row/column. A simple cell may be a string; a structured cell
may use text,rowspan,colspan,header. Use at least two logical columns and one
row, with every declared measure cell containing a numeric value.
Only actual header cells use header=true; numeric measure cells in body rows
must remain non-header cells.
Transcribe printed values exactly. If a plotted mark has no printed value but a
visible labelled axis and tick scale bound it, read it to no more precision than
that scale supports, prefix that cell with ≈, and set estimated to true. Exact
printed cells remain unprefixed even in a table that also contains estimates.
Never estimate from an unlabelled, cropped, ambiguous, or non-linear scale;
never extrapolate beyond visible ticks, invent a value, or emit a range. Put a
currency symbol or unit in the measure-column header, not after an estimate
marker in a value cell. Preserve a visibly printed N/A or dash as an explicit
missing cell, but every table must contain at least one numeric measure. Return
JSON only, without prose/commentary."""


class BlockRepairError(ValueError):
    """A stable failure while validating or applying one block repair."""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("nonfinite JSON value")


_MODEL_JSON_DECODER = json.JSONDecoder(
    object_pairs_hook=_strict_json_object,
    parse_constant=_reject_json_constant,
)


def _decode_single_model_object(content: str) -> dict[str, Any]:
    """Decode one object while tolerating bounded non-JSON model boilerplate."""

    value = content.strip()
    try:
        payload = _MODEL_JSON_DECODER.decode(value)
    except (json.JSONDecodeError, ValueError, RecursionError):
        payload = None
    if isinstance(payload, dict):
        return payload
    if payload is not None:
        raise BlockRepairError("filex_block_repair_invalid_json")

    start = value.find("{")
    if start < 0 or start > MAX_MODEL_BOILERPLATE_CHARS:
        raise BlockRepairError("filex_block_repair_invalid_json")
    try:
        payload, end = _MODEL_JSON_DECODER.raw_decode(value, start)
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise BlockRepairError("filex_block_repair_invalid_json") from None
    if not isinstance(payload, dict):
        raise BlockRepairError("filex_block_repair_invalid_json")
    prefix = value[:start].strip()
    suffix = value[end:].strip()
    boilerplate = prefix + suffix
    if (
        len(prefix) + len(suffix) > MAX_MODEL_BOILERPLATE_CHARS
        or "{" in boilerplate
        or "}" in boilerplate
    ):
        raise BlockRepairError("filex_block_repair_invalid_json")
    return payload


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
class StructuredCell:
    text: str
    rowspan: int = 1
    colspan: int = 1
    header: bool = False


@dataclass(frozen=True, slots=True)
class StructuredTable:
    columns: tuple[StructuredCell | str, ...]
    rows: tuple[tuple[StructuredCell | str, ...], ...]
    caption: str | None = None
    labels: tuple[str, ...] = ()
    estimated: bool = False
    value_columns: tuple[int, ...] = ()
    notes: tuple[str, ...] = ()

    @classmethod
    def from_model_output(
        cls, content: str, *, require_numeric: bool
    ) -> "StructuredTable":
        if not isinstance(content, str) or not content.strip():
            raise BlockRepairError("filex_block_repair_empty_output")
        if len(content) > MAX_MODEL_OUTPUT_CHARS:
            raise BlockRepairError("filex_block_repair_output_too_large")
        payload = _decode_single_model_object(content)
        allowed = {"columns", "rows", "caption", "notes"}
        required = {"columns", "rows"}
        if require_numeric:
            allowed.update({"labels", "estimated", "value_columns"})
            required.update({"labels", "estimated", "value_columns"})
        if (
            not isinstance(payload, dict)
            or not required.issubset(payload)
            or set(payload) - allowed
        ):
            raise BlockRepairError("filex_block_repair_schema_invalid")
        columns = payload["columns"]
        rows = payload["rows"]
        if (
            not isinstance(columns, list)
            or not 1 <= len(columns) <= MAX_COLUMNS
            or not isinstance(rows, list)
            or not 1 <= len(rows) <= MAX_ROWS
        ):
            raise BlockRepairError("filex_block_repair_shape_invalid")
        normalized_columns = tuple(
            _structured_cell(cell, default_header=True) for cell in columns
        )
        if any(not column.text or not column.header for column in normalized_columns):
            raise BlockRepairError("filex_block_repair_header_invalid")
        normalized_rows: list[tuple[StructuredCell, ...]] = []
        for row in rows:
            if not isinstance(row, list):
                raise BlockRepairError("filex_block_repair_row_invalid")
            normalized_rows.append(
                tuple(_structured_cell(cell, default_header=False) for cell in row)
            )
        _validate_table_grid(normalized_columns, normalized_rows)
        if not any(not cell.header for row in normalized_rows for cell in row):
            raise BlockRepairError("filex_block_repair_body_invalid")

        raw_caption = payload.get("caption")
        caption = None if raw_caption in (None, "") else _cell_text(raw_caption)
        raw_notes = payload.get("notes", [])
        if not isinstance(raw_notes, list) or len(raw_notes) > 32:
            raise BlockRepairError("filex_block_repair_notes_invalid")
        notes = tuple(_cell_text(note) for note in raw_notes)
        if any(not note for note in notes) or len(set(notes)) != len(notes):
            raise BlockRepairError("filex_block_repair_notes_invalid")
        labels: tuple[str, ...] = ()
        estimated = False
        value_columns: tuple[int, ...] = ()
        if require_numeric:
            raw_labels = payload["labels"]
            if not isinstance(raw_labels, list) or not raw_labels:
                raise BlockRepairError("filex_block_repair_schema_invalid")
            labels = tuple(_cell_text(label) for label in raw_labels)
            if any(not label for label in labels) or len(set(labels)) != len(labels):
                raise BlockRepairError("filex_chart_repair_labels_invalid")
            raw_estimated = payload["estimated"]
            if not isinstance(raw_estimated, bool):
                raise BlockRepairError("filex_block_repair_schema_invalid")
            estimated = raw_estimated
            raw_value_columns = payload["value_columns"]
            if (
                not isinstance(raw_value_columns, list)
                or not raw_value_columns
                or any(
                    isinstance(index, bool) or not isinstance(index, int)
                    for index in raw_value_columns
                )
                or len(set(raw_value_columns)) != len(raw_value_columns)
            ):
                raise BlockRepairError("filex_chart_repair_value_columns_invalid")
            value_columns = tuple(raw_value_columns)
            _validate_chart_table(
                normalized_columns,
                normalized_rows,
                caption=caption,
                labels=labels,
                estimated=estimated,
                value_columns=value_columns,
            )
        return cls(
            columns=normalized_columns,
            rows=tuple(normalized_rows),
            caption=caption,
            labels=labels,
            estimated=estimated,
            value_columns=value_columns,
            notes=notes,
        )

    def to_html(self) -> str:
        columns = tuple(
            _structured_cell(cell, default_header=True) for cell in self.columns
        )
        rows = [
            tuple(_structured_cell(cell, default_header=False) for cell in row)
            for row in self.rows
        ]
        _validate_table_grid(columns, rows)
        caption = (
            f"<caption>{escape(_cell_text(self.caption))}</caption>"
            if self.caption
            else ""
        )
        header = "".join(_cell_html(cell) for cell in columns)
        body = "".join(
            "<tr>" + "".join(_cell_html(cell) for cell in row) + "</tr>" for row in rows
        )
        table = (
            f"<table>{caption}<thead><tr>{header}</tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
        notes = "".join(f"<p>{escape(_cell_text(note))}</p>" for note in self.notes)
        return (
            f'{table}<div class="filex-structured-notes">{notes}</div>'
            if notes
            else table
        )


def _estimate_evidence_declared(content: str) -> bool:
    """Detect an estimate flag or marker without trusting the response shape."""

    value = content.strip()
    if value.startswith("```"):
        value = _FENCE.sub("", value).strip()
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("estimated") is True:
        return True
    rows = payload.get("rows")

    def cell_declares_estimate(cell: Any) -> bool:
        if isinstance(cell, str):
            text = cell
        elif isinstance(cell, dict) and isinstance(cell.get("text"), str):
            text = cell["text"]
        else:
            return False
        return text.strip().startswith(("~", "≈"))

    return isinstance(rows, list) and any(
        cell_declares_estimate(cell)
        for row in rows
        if isinstance(row, list)
        for cell in row
    )


def _validated_model_table(
    content: str,
    *,
    require_numeric: bool,
    estimate_evidence_seen: bool = False,
) -> StructuredTable:
    table = StructuredTable.from_model_output(
        content,
        require_numeric=require_numeric,
    )
    if require_numeric and estimate_evidence_seen and not table.estimated:
        raise BlockRepairError("filex_chart_repair_estimated_value_unverified")
    return table


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


def _cell_span(value: Any, *, field: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise BlockRepairError(f"filex_block_repair_{field}_invalid")
    return value


def _structured_cell(value: Any, *, default_header: bool) -> StructuredCell:
    if isinstance(value, StructuredCell):
        return value
    if not isinstance(value, dict):
        return StructuredCell(text=_cell_text(value), header=default_header)
    if "text" not in value or set(value) - {"text", "rowspan", "colspan", "header"}:
        raise BlockRepairError("filex_block_repair_cell_invalid")
    header = value.get("header", default_header)
    if not isinstance(header, bool):
        raise BlockRepairError("filex_block_repair_cell_header_invalid")
    return StructuredCell(
        text=_cell_text(value["text"]),
        rowspan=_cell_span(
            value.get("rowspan", 1), field="rowspan", maximum=MAX_ROWS + 1
        ),
        colspan=_cell_span(
            value.get("colspan", 1), field="colspan", maximum=MAX_COLUMNS
        ),
        header=header,
    )


def _validate_table_grid(
    columns: tuple[StructuredCell, ...], rows: list[tuple[StructuredCell, ...]]
) -> None:
    _table_grid(columns, rows)


def _table_grid(
    columns: tuple[StructuredCell, ...], rows: list[tuple[StructuredCell, ...]]
) -> tuple[int, dict[tuple[int, int], StructuredCell]]:
    width = sum(cell.colspan for cell in columns)
    if not 2 <= width <= MAX_COLUMNS:
        raise BlockRepairError("filex_block_repair_shape_invalid")
    all_rows = [columns, *rows]
    occupied: dict[tuple[int, int], StructuredCell] = {}
    for row_index, row in enumerate(all_rows):
        column_index = 0
        for cell in row:
            while (row_index, column_index) in occupied:
                column_index += 1
            if column_index + cell.colspan > width or row_index + cell.rowspan > len(
                all_rows
            ):
                raise BlockRepairError("filex_block_repair_span_invalid")
            positions = {
                (covered_row, column)
                for covered_row in range(row_index, row_index + cell.rowspan)
                for column in range(column_index, column_index + cell.colspan)
            }
            if set(occupied) & positions:
                raise BlockRepairError("filex_block_repair_span_invalid")
            occupied.update((position, cell) for position in positions)
            column_index += cell.colspan
        while (row_index, column_index) in occupied:
            column_index += 1
        if column_index != width:
            raise BlockRepairError("filex_block_repair_row_invalid")
    return width, occupied


def _cell_html(cell: StructuredCell) -> str:
    tag = "th" if cell.header else "td"
    attributes = ""
    if cell.rowspan > 1:
        attributes += f' rowspan="{cell.rowspan}"'
    if cell.colspan > 1:
        attributes += f' colspan="{cell.colspan}"'
    return f"<{tag}{attributes}>{escape(cell.text)}</{tag}>"


def _normalized_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _validate_chart_table(
    columns: tuple[StructuredCell, ...],
    rows: list[tuple[StructuredCell, ...]],
    *,
    caption: str | None,
    labels: tuple[str, ...],
    estimated: bool,
    value_columns: tuple[int, ...],
) -> None:
    normalized = tuple(_normalized_label(cell.text) for cell in columns)
    if len(set(normalized)) != len(normalized):
        raise BlockRepairError("filex_chart_repair_header_ambiguous")
    header_has_label = any(
        _SEMANTIC_HEADER.search(column) and _NUMERIC.fullmatch(column) is None
        for column in normalized
    )
    row_has_label = any(
        cell.text.strip() and _NUMERIC.fullmatch(cell.text.strip()) is None
        for row in rows
        for cell in row
    )
    if not header_has_label and not row_has_label:
        raise BlockRepairError("filex_chart_repair_labels_missing")
    width, grid = _table_grid(columns, rows)
    if any(index < 0 or index >= width for index in value_columns):
        raise BlockRepairError("filex_chart_repair_value_columns_invalid")
    contains_marked_estimate = False
    contains_numeric_value = False
    for row_index, row in enumerate(rows, start=1):
        for cell in row:
            value = re.sub(r"\s+", " ", cell.text).strip()
            if _NUMERIC_RANGE.fullmatch(value):
                raise BlockRepairError("filex_chart_repair_range_invalid")
            if _NARRATIVE_ESTIMATE.search(value):
                raise BlockRepairError("filex_chart_repair_narrative_value_invalid")
        for column_index in value_columns:
            measure_cell = grid[(row_index, column_index)]
            if measure_cell.header:
                raise BlockRepairError("filex_chart_repair_measure_header_invalid")
            value = re.sub(r"\s+", " ", measure_cell.text).strip()
            if _NUMERIC_RANGE.fullmatch(value):
                raise BlockRepairError("filex_chart_repair_range_invalid")
            if _NARRATIVE_ESTIMATE.search(value):
                raise BlockRepairError("filex_chart_repair_narrative_value_invalid")
            if _MARKED_CURRENCY_VALUE.match(value):
                raise BlockRepairError("filex_chart_repair_estimated_currency_inline")
            if _MISSING_CHART_VALUE.fullmatch(value):
                continue
            if _NUMERIC.fullmatch(value) is None:
                raise BlockRepairError("filex_chart_repair_numeric_value_missing")
            contains_numeric_value = True
            if value.lstrip().startswith(("~", "≈")):
                contains_marked_estimate = True
        category_count = sum(
            bool(grid[(row_index, column_index)].text.strip())
            for column_index in range(width)
            if column_index not in value_columns
        )
        if category_count == 0 and len(value_columns) < 2:
            raise BlockRepairError("filex_chart_repair_row_labels_missing")

    # The explicit flag and per-cell marker must agree. This makes visually read
    # values transparent without rejecting legitimate chart-to-table extraction.
    if estimated != contains_marked_estimate:
        raise BlockRepairError("filex_chart_repair_estimated_value_unverified")
    if not contains_numeric_value:
        raise BlockRepairError("filex_chart_repair_numeric_value_missing")

    if labels:
        visible = [
            _normalized_label(value)
            for value in (
                *((caption,) if caption else ()),
                *(cell.text for cell in columns),
                *(cell.text for row in rows for cell in row),
            )
            if value
        ]
        for label in labels:
            normalized_label = _normalized_label(label)
            if not any(
                normalized_label in candidate or candidate in normalized_label
                for candidate in visible
                if candidate
            ):
                raise BlockRepairError("filex_chart_repair_label_missing")


def _gateway_options(
    *,
    prompt: str,
    kind: str,
    timeout_seconds: int | None = None,
    increase_output_tokens: bool = False,
) -> dict[str, Any]:
    configured_max_tokens = int(os.getenv("FILEX_BLOCK_REPAIR_MAX_TOKENS", "4096"))
    if configured_max_tokens < 1:
        raise BlockRepairError("filex_block_repair_max_tokens_invalid")
    max_tokens = min(configured_max_tokens, MAX_REPAIR_OUTPUT_TOKENS)
    if increase_output_tokens:
        max_tokens = min(max_tokens * 2, MAX_REPAIR_OUTPUT_TOKENS)
    options: dict[str, Any] = {
        "prompt": prompt,
        "timeout_seconds": (
            timeout_seconds
            if timeout_seconds is not None
            else int(os.getenv("FILEX_BLOCK_REPAIR_TIMEOUT_SECONDS", "180"))
        ),
        "max_tokens": max_tokens,
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


def _correction_prompt(prompt: str, *, reason: str) -> str:
    return (
        f"{prompt}\nCORRECTION ({reason}): the prior response violated the strict "
        "JSON or physical rectangular-grid contract. Read the image again, apply "
        "the header/rowspan/colspan rules above, and return exactly one JSON object."
    )


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
    before_candidates: list[str] | None = None,
    after_candidates: list[str] | None = None,
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
            contextual = _contextual_rewrite_position(
                content,
                positions,
                before_candidates=before_candidates,
                after_candidates=after_candidates,
            )
            if contextual is None:
                raise BlockRepairError(f"filex_block_repair_{scope}_anchor_ambiguous")
            positions = {contextual}
        start, end = next(iter(positions))
        return content[:start] + replacement + content[end:]
    visible_content, content_spans = _visible_projection_with_spans(content)
    visible_positions: set[tuple[int, int]] = set()
    for candidate in values:
        visible_candidate, _candidate_spans = _visible_projection_with_spans(candidate)
        if len(visible_candidate) < 32:
            continue
        start = 0
        while True:
            index = visible_content.find(visible_candidate, start)
            if index < 0:
                break
            raw_start = content_spans[index][0]
            raw_end = content_spans[index + len(visible_candidate) - 1][1]
            table_start = content.lower().rfind("<table", 0, raw_start + 1)
            table_end_before = content.lower().rfind("</table>", 0, raw_start + 1)
            if table_start > table_end_before:
                table_end = content.lower().find("</table>", raw_end)
                if table_end >= 0:
                    raw_start = table_start
                    raw_end = table_end + len("</table>")
            visible_positions.add((raw_start, raw_end))
            start = index + 1
    if len(visible_positions) > 1:
        raise BlockRepairError(f"filex_block_repair_{scope}_anchor_ambiguous")
    if len(visible_positions) == 1:
        start, end = next(iter(visible_positions))
        return content[:start] + replacement + content[end:]
    raise BlockRepairError("filex_block_repair_anchor_missing")


def _exact_candidate_positions(
    content: str, candidates: list[str] | None
) -> set[tuple[int, int]]:
    positions: set[tuple[int, int]] = set()
    for candidate in {
        value for value in candidates or [] if isinstance(value, str) and value.strip()
    }:
        start = 0
        while True:
            index = content.find(candidate, start)
            if index < 0:
                break
            positions.add((index, index + len(candidate)))
            start = index + 1
    return positions


def _contextual_rewrite_position(
    content: str,
    target_positions: set[tuple[int, int]],
    *,
    before_candidates: list[str] | None,
    after_candidates: list[str] | None,
) -> tuple[int, int] | None:
    """Use unique adjacent item content only to disambiguate a known target."""

    before_positions = _exact_candidate_positions(content, before_candidates)
    after_positions = _exact_candidate_positions(content, after_candidates)
    unique_before = next(iter(before_positions)) if len(before_positions) == 1 else None
    unique_after = next(iter(after_positions)) if len(after_positions) == 1 else None
    if unique_before is None and unique_after is None:
        return None
    candidates = {
        position
        for position in target_positions
        if (unique_before is None or unique_before[1] <= position[0])
        and (unique_after is None or position[1] <= unique_after[0])
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def _visible_projection_with_spans(value: str) -> tuple[str, list[tuple[int, int]]]:
    """Project visible text while retaining raw spans for a unique rewrite."""

    characters: list[str] = []
    spans: list[tuple[int, int]] = []

    def append(text: str, start: int, end: int) -> None:
        for character in text.lower():
            if character in "#>*_`":
                continue
            if character.isspace():
                if not characters or characters[-1] == " ":
                    if spans:
                        spans[-1] = (spans[-1][0], end)
                    continue
                characters.append(" ")
                spans.append((start, end))
                continue
            characters.append(character)
            spans.append((start, end))

    index = 0
    while index < len(value):
        if value[index] == "<":
            closing = value.find(">", index + 1)
            if closing >= 0:
                append(" ", index, closing + 1)
                index = closing + 1
                continue
        if value[index] == "&":
            closing = value.find(";", index + 1, min(len(value), index + 32))
            if closing >= 0:
                encoded = value[index : closing + 1]
                decoded = unescape(encoded)
                if decoded != encoded:
                    append(decoded, index, closing + 1)
                    index = closing + 1
                    continue
        append(value[index], index, index + 1)
        index += 1

    while characters and characters[0] == " ":
        characters.pop(0)
        spans.pop(0)
    while characters and characters[-1] == " ":
        characters.pop()
        spans.pop()
    return "".join(characters), spans


def _anchored_insert(
    content: str,
    candidates: list[str],
    insertion: str,
    *,
    before: bool,
    scope: str,
) -> str:
    values = {value for value in candidates if isinstance(value, str) and value.strip()}
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
    candidates: list[str] = []
    for value in (item.get("md"), item.get("html"), item.get("value")):
        if not isinstance(value, str):
            continue
        candidates.append(value)
        canonical = normalize_structured_tables(value)
        if canonical != value:
            candidates.append(canonical)
    return candidates


def _rewrite_neighbor_candidates(
    page: dict[str, Any], item_index: int
) -> tuple[list[str] | None, list[str] | None]:
    items = page.get("items")
    if not isinstance(items, list):
        raise BlockRepairError("filex_block_repair_target_items_invalid")

    def nearest(indices: range) -> list[str] | None:
        for index in indices:
            candidate = items[index]
            if not isinstance(candidate, dict):
                continue
            values = _item_candidates(candidate)
            if any(value.strip() for value in values):
                return values
        return None

    return (
        nearest(range(item_index - 1, -1, -1)),
        nearest(range(item_index + 1, len(items))),
    )


def _visible_context(value: str) -> str:
    visible = unescape(re.sub(r"<[^>]+>", " ", value))
    visible = re.sub(r"(?:^|\s)[#>*_`~]+", " ", visible)
    return re.sub(r"\s+", " ", visible).strip().casefold()


def _represented_by_adjacent_item(
    page: dict[str, Any], item_index: int, value: str
) -> bool:
    items = page.get("items")
    if not isinstance(items, list):
        return False
    expected = _visible_context(value)
    if not expected:
        return False
    for candidate_index in (item_index - 1, item_index + 1):
        if not 0 <= candidate_index < len(items):
            continue
        candidate = items[candidate_index]
        if not isinstance(candidate, dict):
            continue
        for content in _item_candidates(candidate):
            actual = _visible_context(content)
            if actual and (expected in actual or actual in expected):
                return True
    return False


def _deduplicate_structured_context(
    table: StructuredTable, *, page: dict[str, Any], item_index: int
) -> StructuredTable:
    caption = table.caption
    if caption and _represented_by_adjacent_item(page, item_index, caption):
        caption = None
    notes = tuple(
        note
        for note in table.notes
        if not _represented_by_adjacent_item(page, item_index, note)
    )
    if caption == table.caption and notes == table.notes:
        return table
    return replace(table, caption=caption, notes=notes)


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


def _neighbor_anchor(items: list[Any], item_index: int) -> tuple[dict[str, Any], bool]:
    """Find a nearby non-empty item without changing established item order."""

    for distance in range(1, len(items) + 1):
        for candidate_index in (item_index + distance, item_index - distance):
            if not 0 <= candidate_index < len(items):
                continue
            candidate = items[candidate_index]
            if not isinstance(candidate, dict):
                continue
            if not any(value.strip() for value in _item_candidates(candidate)):
                continue
            return candidate, candidate_index > item_index
    raise BlockRepairError("filex_block_repair_anchor_missing")


def _new_chart_item(
    page: dict[str, Any], issue: dict[str, Any]
) -> tuple[dict[str, Any], int, dict[str, Any] | None, bool]:
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
    target_key = _bbox_key(item)
    insertion_index = len(items)
    for index, existing in enumerate(items):
        if isinstance(existing, dict) and _bbox_key(existing) > target_key:
            insertion_index = index
            break
    items.insert(insertion_index, item)
    try:
        neighbor, insert_before = _neighbor_anchor(items, insertion_index)
    except BlockRepairError as exc:
        if str(exc) != "filex_block_repair_anchor_missing":
            raise
        neighbor, insert_before = None, False
    issue["item_index"] = insertion_index
    return item, insertion_index, neighbor, insert_before


def _target_item(
    layout: dict[str, Any], issue: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    page = _target_page(layout, issue)
    item_index = issue.get("item_index")
    items = page.get("items")
    block_id = issue.get("block_id")
    if not isinstance(items, list):
        raise BlockRepairError("filex_block_repair_target_items_invalid")
    if not isinstance(block_id, str) or not block_id.strip():
        raise BlockRepairError("filex_block_repair_target_identity_missing")
    matches = [
        (index, candidate)
        for index, candidate in enumerate(items)
        if isinstance(candidate, dict) and candidate.get("id") == block_id
    ]
    if len(matches) > 1:
        raise BlockRepairError("filex_block_repair_target_identity_ambiguous")
    if len(matches) == 1:
        current_index, item = matches[0]
        issue["item_index"] = current_index
        return page, item
    if item_index is None and issue.get("reason") == "filex_chart_content_unusable":
        item, _index, _neighbor, _before = _new_chart_item(page, issue)
        return page, item
    if (
        isinstance(item_index, int)
        and not isinstance(item_index, bool)
        and 0 <= item_index < len(items)
    ):
        raise BlockRepairError("filex_block_repair_target_identity_mismatch")
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


def _parsed_page_neighbor_anchor(
    layout: dict[str, Any], page_number: int
) -> tuple[list[str], bool]:
    pages = layout.get("pages")
    if not isinstance(pages, list):
        raise BlockRepairError("filex_block_repair_anchor_missing")
    ordered = sorted(
        (
            page
            for page in pages
            if isinstance(page, dict)
            and isinstance(page.get("page_index"), int)
            and not isinstance(page.get("page_index"), bool)
        ),
        key=lambda page: page["page_index"],
    )
    target_index = next(
        (
            index
            for index, page in enumerate(ordered)
            if page["page_index"] == page_number - 1
        ),
        None,
    )
    if target_index is None:
        raise BlockRepairError("filex_block_repair_anchor_missing")
    for distance in range(1, len(ordered) + 1):
        for candidate_index in (target_index + distance, target_index - distance):
            if not 0 <= candidate_index < len(ordered):
                continue
            markdown = ordered[candidate_index].get("markdown")
            if not isinstance(markdown, str) or not markdown.strip():
                continue
            candidates = [markdown]
            canonical = normalize_structured_tables(markdown)
            if canonical != markdown:
                candidates.append(canonical)
            return candidates, candidate_index > target_index
    raise BlockRepairError("filex_block_repair_anchor_missing")


def _item_table_candidates(item: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for value in (item.get("html"), item.get("md"), item.get("value")):
        if not isinstance(value, str) or not value.strip():
            continue
        canonical = normalize_structured_tables(value)
        if _HTML_TABLE_BLOCK.search(canonical) is None:
            continue
        for candidate in (value, canonical):
            if candidate.strip() and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _item_table_content(item: dict[str, Any]) -> str:
    for candidate in _item_table_candidates(item):
        canonical = normalize_structured_tables(candidate).strip()
        if _HTML_TABLE_BLOCK.search(canonical) is not None:
            return canonical
    raise BlockRepairError("filex_block_repair_document_source_missing")


def _candidate_occurrence_count(content: str, candidates: list[str]) -> int:
    spans: list[tuple[int, int]] = []
    for candidate in {
        value for value in candidates if isinstance(value, str) and value.strip()
    }:
        start = 0
        while True:
            index = content.find(candidate, start)
            if index < 0:
                break
            spans.append((index, index + len(candidate)))
            start = index + 1
    if not spans:
        return 0
    groups = 0
    group_end = -1
    for start, end in sorted(spans):
        if start >= group_end:
            groups += 1
            group_end = end
        else:
            group_end = max(group_end, end)
    return groups


def _matching_table_item_count(items: Any, table_content: str) -> int:
    if not isinstance(items, list):
        raise BlockRepairError("filex_block_repair_target_items_invalid")
    count = 0
    for candidate in items:
        if not isinstance(candidate, dict):
            continue
        try:
            candidate_content = _item_table_content(candidate)
        except BlockRepairError as exc:
            if str(exc) != "filex_block_repair_document_source_missing":
                raise
            continue
        if candidate_content == table_content:
            count += 1
    return count


def _coverage_scope_insert(
    content: str,
    *,
    target_candidates: list[str],
    insertion: str,
    expected_count: int,
    neighbor_candidates: list[str] | None,
    insert_before: bool,
    scope: str,
) -> str:
    if _candidate_occurrence_count(content, target_candidates) >= expected_count:
        return content
    if neighbor_candidates:
        return _anchored_insert(
            content,
            neighbor_candidates,
            insertion,
            before=insert_before,
            scope=scope,
        )
    if not content.strip():
        return insertion
    raise BlockRepairError("filex_block_repair_anchor_missing")


def apply_document_coverage_repair(
    *, document: str, layout: dict[str, Any], issue: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Project one validated item table into every missing ParseOutput view."""

    if issue.get("reason") != "filex_document_table_coverage_incomplete":
        raise BlockRepairError("filex_block_repair_document_anchor_required")
    updated_layout = copy.deepcopy(layout)
    updated_issue = dict(issue)
    page, item = _target_item(updated_layout, updated_issue)
    item_index = updated_issue.get("item_index")
    if not isinstance(item_index, int) or isinstance(item_index, bool):
        raise BlockRepairError("filex_block_repair_target_missing")
    if item.get("type") not in {"table", "chart"}:
        raise BlockRepairError("filex_block_repair_document_source_missing")

    insertion = _item_table_content(item)
    target_candidates = _item_table_candidates(item)
    if insertion not in target_candidates:
        target_candidates.append(insertion)
    items = page.get("items")
    page_expected = _matching_table_item_count(items, insertion)
    all_items = [
        candidate
        for layout_page in updated_layout.get("layout_pages", [])
        if isinstance(layout_page, dict)
        for candidate in (
            layout_page.get("items")
            if isinstance(layout_page.get("items"), list)
            else []
        )
    ]
    document_expected = _matching_table_item_count(all_items, insertion)
    if page_expected < 1 or document_expected < 1:
        raise BlockRepairError("filex_block_repair_document_source_missing")

    neighbor: dict[str, Any] | None
    insert_before: bool
    try:
        neighbor, insert_before = _neighbor_anchor(items, item_index)
    except BlockRepairError as exc:
        if str(exc) != "filex_block_repair_anchor_missing":
            raise
        neighbor, insert_before = None, False
    neighbor_candidates = _item_candidates(neighbor) if neighbor is not None else None

    page_markdown = page.get("md")
    if not isinstance(page_markdown, str):
        raise BlockRepairError("filex_block_repair_page_markdown_missing")
    page_number = page.get("page_number", page.get("page"))
    if not isinstance(page_number, int) or isinstance(page_number, bool):
        raise BlockRepairError("filex_block_repair_target_page_invalid")
    parsed_page = _matching_parsed_page(updated_layout, page_number)
    parsed_markdown: str | None = None
    if parsed_page is not None:
        parsed_markdown = parsed_page.get("markdown")
        if not isinstance(parsed_markdown, str):
            raise BlockRepairError("filex_block_repair_parsed_markdown_missing")

    page["md"] = _coverage_scope_insert(
        page_markdown,
        target_candidates=target_candidates,
        insertion=insertion,
        expected_count=page_expected,
        neighbor_candidates=neighbor_candidates,
        insert_before=insert_before,
        scope="page",
    )
    if parsed_page is not None and parsed_markdown is not None:
        parsed_page["markdown"] = _coverage_scope_insert(
            parsed_markdown,
            target_candidates=target_candidates,
            insertion=insertion,
            expected_count=page_expected,
            neighbor_candidates=neighbor_candidates,
            insert_before=insert_before,
            scope="parsed_page",
        )

    if _candidate_occurrence_count(document, target_candidates) < document_expected:
        if neighbor_candidates is not None:
            document = _anchored_insert(
                document,
                neighbor_candidates,
                insertion,
                before=insert_before,
                scope="document",
            )
        elif not document.strip():
            document = insertion
        else:
            page_anchor, before_page = _parsed_page_neighbor_anchor(
                updated_layout, page_number
            )
            document = _anchored_insert(
                document,
                page_anchor,
                insertion,
                before=before_page,
                scope="document_page",
            )

    updated_layout["markdown"] = document
    issue["item_index"] = item_index
    return document, updated_layout


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
        item_index = updated_issue.get("item_index")
    if not isinstance(item_index, int) or isinstance(item_index, bool):
        raise BlockRepairError("filex_block_repair_target_missing")
    render_table = _deduplicate_structured_context(
        table, page=page, item_index=item_index
    )
    html = render_table.to_html()
    old_candidates = _item_candidates(item)
    insertion_required = created or not any(
        candidate.strip() for candidate in old_candidates
    )
    whole_empty_scope = False
    if insertion_required and neighbor is None:
        items = page.get("items")
        if not isinstance(items, list) or not isinstance(item_index, int):
            raise BlockRepairError("filex_block_repair_target_items_invalid")
        try:
            neighbor, insert_before = _neighbor_anchor(items, item_index)
        except BlockRepairError as exc:
            if str(exc) != "filex_block_repair_anchor_missing":
                raise
            whole_empty_scope = True
    anchor_candidates = (
        _item_candidates(neighbor)
        if insertion_required and neighbor is not None
        else old_candidates
    )
    before_candidates: list[str] | None = None
    after_candidates: list[str] | None = None
    if not insertion_required:
        before_candidates, after_candidates = _rewrite_neighbor_candidates(
            page, item_index
        )
    page_markdown = page.get("md")
    if not isinstance(page_markdown, str):
        raise BlockRepairError("filex_block_repair_page_markdown_missing")
    page_number = page.get("page_number", page.get("page"))
    if not isinstance(page_number, int) or isinstance(page_number, bool):
        raise BlockRepairError("filex_block_repair_target_page_invalid")
    parsed_page = _matching_parsed_page(updated_layout, page_number)
    parsed_markdown: str | None = None
    if parsed_page is not None:
        parsed_markdown = parsed_page.get("markdown")
        if not isinstance(parsed_markdown, str):
            raise BlockRepairError("filex_block_repair_parsed_markdown_missing")

    if whole_empty_scope:
        if page_markdown.strip() or (
            parsed_markdown is not None and parsed_markdown.strip()
        ):
            raise BlockRepairError("filex_block_repair_anchor_missing")
        if document.strip():
            page_anchor, before_page = _parsed_page_neighbor_anchor(
                updated_layout, page_number
            )
            document = _anchored_insert(
                document,
                page_anchor,
                html,
                before=before_page,
                scope="document_page",
            )
        else:
            document = html
        page["md"] = html
        if parsed_page is not None:
            parsed_page["markdown"] = html
    elif insertion_required:
        document = _anchored_insert(
            document, anchor_candidates, html, before=insert_before, scope="document"
        )
        page["md"] = _anchored_insert(
            page_markdown, anchor_candidates, html, before=insert_before, scope="page"
        )
        if parsed_page is not None and parsed_markdown is not None:
            parsed_page["markdown"] = _anchored_insert(
                parsed_markdown,
                anchor_candidates,
                html,
                before=insert_before,
                scope="parsed_page",
            )
    else:
        document = _anchored_rewrite(
            document,
            anchor_candidates,
            html,
            scope="document",
            before_candidates=before_candidates,
            after_candidates=after_candidates,
        )
        page["md"] = _anchored_rewrite(
            page_markdown,
            anchor_candidates,
            html,
            scope="page",
            before_candidates=before_candidates,
            after_candidates=after_candidates,
        )
        if parsed_page is not None and parsed_markdown is not None:
            parsed_page["markdown"] = _anchored_rewrite(
                parsed_markdown,
                anchor_candidates,
                html,
                scope="parsed_page",
                before_candidates=before_candidates,
                after_candidates=after_candidates,
            )
    item["md"] = html
    item["html"] = html
    item["value"] = html
    if render_table.value_columns:
        item["filex_chart_value_columns"] = list(render_table.value_columns)
    else:
        item.pop("filex_chart_value_columns", None)
    items = page.get("items")
    if not isinstance(items, list):
        raise BlockRepairError("filex_block_repair_target_items_invalid")
    page["text"] = "\n\n".join(
        value
        for candidate in items
        if isinstance(candidate, dict)
        for value in (candidate.get("value"),)
        if isinstance(value, str) and value
    )
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
    total_timeout_seconds: int | None = None,
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
    if (
        not isinstance(max_blocks, int)
        or isinstance(max_blocks, bool)
        or not 1 <= max_blocks <= MAX_REPAIR_BLOCKS
    ):
        raise BlockRepairError("filex_block_repair_limit_invalid")
    if total_timeout_seconds is not None and (
        isinstance(total_timeout_seconds, bool)
        or not isinstance(total_timeout_seconds, int)
        or not 1 <= total_timeout_seconds <= 900
    ):
        raise BlockRepairError("filex_block_repair_timeout_invalid")
    loop = asyncio.get_running_loop()
    deadline = (
        loop.time() + total_timeout_seconds
        if total_timeout_seconds is not None
        else None
    )
    repaired: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    block_attempted = 0
    with tempfile.TemporaryDirectory(prefix="filex-block-repair-") as temporary:
        temporary_root = Path(temporary)
        selected_issues = issues[:max_blocks]
        for repair_index, (kind, issue) in enumerate(selected_issues):
            remaining_seconds = deadline - loop.time() if deadline is not None else None
            if remaining_seconds is not None and remaining_seconds <= 1:
                break
            block_attempted += 1
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
                estimate_evidence_seen = False
                for attempt in range(MAX_REPAIR_ATTEMPTS):
                    remaining_seconds = (
                        deadline - loop.time() if deadline is not None else None
                    )
                    if remaining_seconds is not None and remaining_seconds <= 1:
                        if attempt == 0:
                            last_reason = "filex_block_repair_deadline_exhausted"
                        break
                    remaining_blocks = max(1, len(selected_issues) - repair_index)
                    configured_timeout = int(
                        os.getenv("FILEX_BLOCK_REPAIR_TIMEOUT_SECONDS", "180")
                    )
                    call_timeout = configured_timeout
                    if remaining_seconds is not None:
                        call_timeout = max(
                            1,
                            min(
                                configured_timeout,
                                int(max(1.0, remaining_seconds - 0.5))
                                // remaining_blocks,
                            ),
                        )
                    attempt_prompt = prompt
                    if attempt:
                        attempt_prompt = _correction_prompt(
                            prompt,
                            reason=last_reason,
                        )
                        if estimate_evidence_seen:
                            attempt_prompt += (
                                " The prior response disclosed a visually estimated value; "
                                "retain estimated=true and a ~ or ≈ marker on every estimated "
                                "cell. Do not launder it into an unmarked exact value."
                            )
                    response_text = ""
                    try:
                        response = await asyncio.wait_for(
                            backend.transcribe(
                                crop_path,
                                media_type="image",
                                file_type="png",
                                source_file_name=crop_path.name,
                                options=_gateway_options(
                                    prompt=attempt_prompt,
                                    kind=kind,
                                    timeout_seconds=call_timeout,
                                    increase_output_tokens=(
                                        attempt > 0
                                        and last_reason
                                        == "filex_block_repair_output_truncated"
                                    ),
                                ),
                            ),
                            timeout=call_timeout,
                        )
                        response_text = str(getattr(response, "text", "") or "")
                        metadata = getattr(response, "metadata", None)
                        finish_reason = (
                            str(metadata.get("finish_reason") or "").strip().lower()
                            if isinstance(metadata, dict)
                            else ""
                        )
                        if finish_reason == "length":
                            if kind == "charts" and _estimate_evidence_declared(
                                response_text
                            ):
                                estimate_evidence_seen = True
                            raise BlockRepairError(
                                "filex_block_repair_output_truncated"
                            )
                        table = _validated_model_table(
                            response_text,
                            require_numeric=kind == "charts",
                            estimate_evidence_seen=estimate_evidence_seen,
                        )
                        break
                    except BlockRepairError as exc:
                        last_reason = str(exc)
                        if (
                            kind == "charts"
                            and last_reason
                            == "filex_chart_repair_estimated_value_unverified"
                            and _estimate_evidence_declared(response_text)
                        ):
                            estimate_evidence_seen = True
                    except TimeoutError:
                        last_reason = "filex_block_repair_backend_timeout"
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
    document_section = quality_report.get("document")
    raw_document_issues = (
        document_section.get("issues") if isinstance(document_section, dict) else []
    )
    document_issues = (
        raw_document_issues if isinstance(raw_document_issues, list) else []
    )
    document_budget = max(0, max_blocks - block_attempted)
    document_attempted = 0
    for raw_issue in document_issues[:document_budget]:
        if deadline is not None and deadline - loop.time() <= 0:
            break
        document_attempted += 1
        issue = raw_issue if isinstance(raw_issue, dict) else {}
        working_issue = dict(issue)
        try:
            if not isinstance(raw_issue, dict):
                raise BlockRepairError("filex_block_repair_document_anchor_required")
            page_number = working_issue.get("page_number")
            block_id = working_issue.get("block_id")
            if (
                working_issue.get("reason")
                != "filex_document_table_coverage_incomplete"
                or not isinstance(page_number, int)
                or isinstance(page_number, bool)
                or not isinstance(block_id, str)
                or not block_id.strip()
            ):
                raise BlockRepairError("filex_block_repair_document_anchor_required")
            document, layout = apply_document_coverage_repair(
                document=document,
                layout=layout,
                issue=working_issue,
            )
            repaired.append(
                {
                    "kind": "document",
                    "page_number": page_number,
                    "item_index": working_issue.get("item_index"),
                    "block_id": block_id,
                }
            )
        except (BlockRepairError, KeyError, TypeError, ValueError) as exc:
            failures.append(
                {
                    "kind": "document",
                    "page_number": issue.get("page_number"),
                    "item_index": issue.get("item_index"),
                    "block_id": issue.get("block_id"),
                    "reason": str(exc)[:128] or type(exc).__name__,
                }
            )
    if raw_document_issues and not isinstance(raw_document_issues, list):
        failures.append(
            {
                "kind": "document",
                "page_number": None,
                "item_index": None,
                "block_id": None,
                "reason": "filex_block_repair_document_anchor_required",
            }
        )
    if isinstance(document_section, dict) and document_section.get("issues_truncated"):
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
        "attempted": block_attempted + document_attempted,
        "remaining": max(0, len(issues) - block_attempted)
        + max(0, len(document_issues) - document_attempted),
    }


async def _main() -> int:
    try:
        request = json.load(sys.stdin)
        raw_total_timeout = request.get("total_timeout_seconds")
        result = await repair_parse_output(
            source_path=Path(request["source_path"]),
            document=str(request["document"]),
            layout=request["layout"],
            quality_report=request["quality_report"],
            max_blocks=int(request.get("max_blocks") or MAX_REPAIR_BLOCKS),
            total_timeout_seconds=(
                int(raw_total_timeout) if raw_total_timeout is not None else None
            ),
        )
        json.dump({"success": True, **result}, sys.stdout, ensure_ascii=False)
        return 0
    except (
        BlockRepairError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        reason = (
            str(exc)
            if isinstance(exc, BlockRepairError)
            else "filex_block_repair_failed"
        )
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
