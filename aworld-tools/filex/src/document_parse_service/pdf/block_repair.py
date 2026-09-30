"""Targeted structured repair for scorer-facing table and chart blocks."""

from __future__ import annotations

import asyncio
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
    r"^[~≈]?\s*[$€£¥]?\s*[-+]?(?:\d[\d, ]*|\d*\.\d+)"
    r"(?:\.\d+)?\s*(?:%|[kKmMbBtT]|million|billion|trillion)?$",
    re.IGNORECASE,
)

_TABLE_PROMPT = """Extract the visible table into strict JSON only.
Return exactly {"columns":["..."],"rows":[["...", "..."]]}.
Use at least two columns and one row. Preserve every visible header and cell.
Repeat merged header values where needed to form a rectangular grid. Do not
return Markdown, HTML, prose, commentary, or invented values."""

_CHART_PROMPT = """Extract every visible chart data point into strict JSON only.
Return exactly {"columns":["..."],"rows":[["...", "..."]]}.
Use one observation per row. Columns must explicitly preserve panel, series,
category/date, numeric value, and unit when visible. Use at least two columns
and one row. For approximate points return one best numeric estimate, optionally
prefixed by ~. Do not return Markdown, HTML, prose, ranges, or invented values."""


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
        if require_numeric and not any(
            _NUMERIC.fullmatch(cell.strip())
            for row in normalized_rows
            for cell in row
        ):
            raise BlockRepairError("filex_chart_repair_numeric_value_missing")
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
    try:
        x = float(bbox["x"]) * image.width / page_width
        y = float(bbox["y"]) * image.height / page_height
        w = float(bbox["w"]) * image.width / page_width
        h = float(bbox["h"]) * image.height / page_height
    except (KeyError, TypeError, ValueError, OverflowError):
        raise BlockRepairError("filex_block_repair_bbox_invalid") from None
    padding_x = max(w * 0.03, 2.0)
    padding_y = max(h * 0.03, 2.0)
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


def _replace_once(content: str, candidates: list[str], replacement: str) -> tuple[str, bool]:
    for candidate in sorted({value for value in candidates if value.strip()}, key=len, reverse=True):
        if candidate in content:
            return content.replace(candidate, replacement, 1), True
    return content, False


def _target_item(layout: dict[str, Any], issue: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    page_number = issue.get("page_number")
    item_index = issue.get("item_index")
    for page in layout.get("layout_pages", []):
        if not isinstance(page, dict) or page.get("page_number", page.get("page")) != page_number:
            continue
        items = page.get("items")
        if (
            isinstance(items, list)
            and isinstance(item_index, int)
            and not isinstance(item_index, bool)
            and 0 <= item_index < len(items)
            and isinstance(items[item_index], dict)
        ):
            return page, items[item_index]
    raise BlockRepairError("filex_block_repair_target_missing")


def apply_structured_repair(
    *,
    document: str,
    layout: dict[str, Any],
    issue: dict[str, Any],
    table: StructuredTable,
) -> tuple[str, dict[str, Any]]:
    page, item = _target_item(layout, issue)
    html = table.to_html()
    old_candidates = [
        value
        for value in (item.get("md"), item.get("html"), item.get("value"))
        if isinstance(value, str)
    ]
    document, replaced = _replace_once(document, old_candidates, html)
    page_markdown = page.get("md")
    if isinstance(page_markdown, str):
        page["md"], page_replaced = _replace_once(page_markdown, old_candidates, html)
        replaced = replaced or page_replaced
    page_text = page.get("text")
    if isinstance(page_text, str):
        page["text"], text_replaced = _replace_once(page_text, old_candidates, html)
        replaced = replaced or text_replaced
    page_number = page.get("page_number", page.get("page"))
    pages = layout.get("pages")
    if isinstance(pages, list):
        for parsed_page in pages:
            if not isinstance(parsed_page, dict):
                continue
            parsed_index = parsed_page.get("page_index")
            if isinstance(page_number, int) and parsed_index == page_number - 1:
                page_markdown = parsed_page.get("markdown")
                if isinstance(page_markdown, str):
                    parsed_page["markdown"], parsed_replaced = _replace_once(
                        page_markdown, old_candidates, html
                    )
                    replaced = replaced or parsed_replaced
                break
    if not replaced:
        document = document.rstrip() + "\n\n" + html + "\n"
        if isinstance(page.get("md"), str):
            page["md"] = page["md"].rstrip() + "\n\n" + html
    item["md"] = html
    item["html"] = html
    item["value"] = html
    layout["markdown"] = document
    return document, layout


def _reconcile_document_tables(
    document: str, layout: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    """Restore structured item HTML omitted from the root Markdown."""

    reconciled: list[dict[str, Any]] = []
    for page in layout.get("layout_pages", []):
        if not isinstance(page, dict):
            continue
        page_number = page.get("page_number", page.get("page"))
        items = page.get("items")
        if not isinstance(items, list):
            continue
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            html = item.get("html")
            if not isinstance(html, str) or "<table" not in html.lower():
                continue
            if html in document:
                continue
            document = document.rstrip() + "\n\n" + html + "\n"
            reconciled.append(
                {
                    "kind": "document",
                    "page_number": page_number,
                    "item_index": item_index,
                    "block_id": item.get("id"),
                }
            )
    if reconciled:
        layout["markdown"] = document
    return document, reconciled


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
                page, _item = _target_item(layout, issue)
                crop_path = crop_renderer(
                    source_path,
                    page_number=page_number,
                    bbox=bbox,
                    page_width=float(page["width"]),
                    page_height=float(page["height"]),
                    output_path=temporary_root / f"block-{repair_index}.png",
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
                document, layout = apply_structured_repair(
                    document=document,
                    layout=layout,
                    issue=issue,
                    table=table,
                )
                repaired.append(
                    {
                        "kind": kind,
                        "page_number": page_number,
                        "item_index": issue.get("item_index"),
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
        document, reconciled = _reconcile_document_tables(document, layout)
        repaired.extend(reconciled)
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
