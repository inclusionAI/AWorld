"""Export FileX Document IR as a provider-neutral ParseOutput document.

This module has no parser, model, dataset, or scorer dependency. Coordinates
come only from the parser's real element boxes. PDF text-layer spans do not
carry complete boxes, so they remain in the original IR instead of becoming
invented layout predictions.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping
from html import escape
from typing import Any


class ParseOutputExportError(ValueError):
    """The source IR cannot be represented without guessing its geometry."""


_SCHEMAS = {"filex-document-ir-v1", "filex-document-ir-v2"}
_MARKDOWN_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_MARKDOWN_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_INLINE_MARKDOWN = re.compile(r"(?<!\\)(?:`|\*\*|__|~~|!\[|\[[^\]]*\]\([^)]*\))")
_LABELS = {
    "caption": "caption",
    "figure-title": "caption",
    "footnote": "footnote",
    "vision-footnote": "footnote",
    "formula": "formula",
    "display-formula": "formula",
    "inline-formula": "formula",
    "list-item": "list-item",
    "list-items": "list-item",
    "page-footer": "page-footer",
    "footer": "page-footer",
    "footer-image": "page-footer",
    "page-header": "page-header",
    "header": "page-header",
    "header-image": "page-header",
    "picture": "picture",
    "image": "picture",
    "chart": "picture",
    "seal": "picture",
    "section-header": "section-header",
    "paragraph-title": "section-header",
    "heading": "section-header",
    "table": "table",
    "text": "text",
    # PaddleOCR-VL emits a full-page OCR text block when layout detection is off.
    "ocr": "text",
    "content": "text",
    "abstract": "text",
    "reference": "text",
    "reference-content": "text",
    "aside-text": "text",
    "vertical-text": "text",
    "number": "text",
    "formula-number": "text",
    "title": "title",
    "doc-title": "title",
    "document-index": "document-index",
    "code": "code",
    "algorithm": "code",
    "checkbox-selected": "checkbox-selected",
    "checkbox-unselected": "checkbox-unselected",
    "form": "form",
    "key-value-region": "key-value-region",
}


def _line_body(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith(("\r", "\n")):
        return line[:-1]
    return line


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\r"):
        return "\r"
    if line.endswith("\n"):
        return "\n"
    return ""


def _markdown_cells(line: str) -> list[str] | None:
    """Split one GFM pipe row without splitting escaped or code-span pipes."""

    value = line.strip()
    if "|" not in value:
        return None
    cells: list[str] = []
    cell: list[str] = []
    delimiter_count = 0
    code_ticks = 0
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            following = value[index + 1]
            if following == "|":
                cell.append("|")
                index += 2
                continue
            cell.extend((character, following))
            index += 2
            continue
        if character == "`":
            end = index + 1
            while end < len(value) and value[end] == "`":
                end += 1
            ticks = end - index
            if code_ticks == 0:
                code_ticks = ticks
            elif ticks == code_ticks:
                code_ticks = 0
            cell.append(value[index:end])
            index = end
            continue
        if character == "|" and code_ticks == 0:
            cells.append("".join(cell).strip())
            cell = []
            delimiter_count += 1
        else:
            cell.append(character)
        index += 1
    cells.append("".join(cell).strip())
    if not delimiter_count:
        return None
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells or None


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    match = _MARKDOWN_FENCE.match(line)
    if match is None:
        return None
    marker = match.group(1)
    return marker[0], len(marker), match.group(2)


def _has_inline_markdown(value: str) -> bool:
    if _INLINE_MARKDOWN.search(value):
        return True
    return bool(
        re.search(r"(?<![\\*])\*[^*\n]+\*(?!\*)", value)
        or re.search(r"(?<![\\_])_[^_\n]+_(?!_)", value)
    )


def _pipe_table_html(headers: list[str], rows: list[list[str]]) -> str:
    header = "".join(f"<th>{escape(cell)}</th>" for cell in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        "<table><thead><tr>"
        + header
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table>"
    )


def normalize_structured_tables(markdown: str) -> str:
    """Canonicalize valid GFM pipe tables consistently in every ParseOutput view."""

    if not markdown or "|" not in markdown:
        return markdown
    lines = markdown.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    fence: tuple[str, int] | None = None
    while index < len(lines):
        body = _line_body(lines[index])
        marker = _fence_marker(body)
        if marker is not None:
            if fence is None:
                fence = marker[:2]
            elif (
                marker[0] == fence[0]
                and marker[1] >= fence[1]
                and not marker[2].strip()
            ):
                fence = None
            output.append(lines[index])
            index += 1
            continue
        if fence is not None or index + 2 >= len(lines):
            output.append(lines[index])
            index += 1
            continue

        separator_line = _line_body(lines[index + 1])
        headers = _markdown_cells(body)
        separators = _markdown_cells(separator_line)
        if (
            body.startswith(("    ", "\t"))
            or separator_line.startswith(("    ", "\t"))
            or not headers
            or separators is None
            or len(separators) != len(headers)
            or not all(_MARKDOWN_SEPARATOR_CELL.fullmatch(cell) for cell in separators)
        ):
            output.append(lines[index])
            index += 1
            continue

        rows: list[list[str]] = []
        end = index + 2
        malformed = False
        while end < len(lines):
            row_line = _line_body(lines[end])
            cells = _markdown_cells(row_line)
            if cells is None:
                break
            if row_line.startswith(("    ", "\t")) or len(cells) != len(headers):
                malformed = True
                break
            rows.append(cells)
            end += 1
        if not rows or malformed:
            output.append(lines[index])
            index += 1
            continue
        if any(_has_inline_markdown(cell) for row in (headers, *rows) for cell in row):
            output.extend(lines[index:end])
            index = end
            continue

        output.append(_pipe_table_html(headers, rows) + _line_ending(lines[end - 1]))
        index = end
    return "".join(output)


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParseOutputExportError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ParseOutputExportError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise ParseOutputExportError(f"{field} must be a finite number")
    return number


def _index(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ParseOutputExportError(f"{field} must be a nonnegative integer")
    return value


def _orientation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseOutputExportError(
            "original_orientation_angle must be 0, 90, 180, or 270"
        )
    normalized = value % 360
    if normalized not in {0, 90, 180, 270}:
        raise ParseOutputExportError(
            "original_orientation_angle must be 0, 90, 180, or 270"
        )
    return normalized


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ParseOutputExportError(f"{field} must be a string")
    return value


def _segment(
    element: Mapping[str, Any], *, width: float, height: float
) -> dict[str, Any]:
    bbox = element.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ParseOutputExportError("element bbox must contain pixel x1, y1, x2, y2")
    x1, y1, x2, y2 = (_number(value, "bbox coordinate") for value in bbox)
    if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1 or x2 > width or y2 > height:
        raise ParseOutputExportError(
            "element bbox must have positive extents within its page"
        )
    raw_label = _text(element.get("type"), "element type")
    label_key = "-".join(raw_label.strip().lower().replace("_", " ").split())
    if label_key not in _LABELS:
        raise ParseOutputExportError(f"unsupported layout label: {raw_label!r}")
    segment = {
        "x": x1,
        "y": y1,
        "w": x2 - x1,
        "h": y2 - y1,
        "label": _LABELS[label_key],
    }
    if element.get("confidence") is not None:
        confidence = _number(element["confidence"], "element confidence")
        if not 0 <= confidence <= 1:
            raise ParseOutputExportError(
                "element confidence must be between zero and one"
            )
        segment["confidence"] = confidence
    return segment


def document_ir_to_parse_output(
    document_ir: Mapping[str, Any],
    *,
    markdown: str,
    example_id: str,
    pipeline_name: str = "filex",
) -> dict[str, Any]:
    """Convert real pixel xyxy elements into ParseOutput layout_pages/xywh.

    Non-table Markdown is preserved exactly; valid GFM pipe tables are converted
    once to deterministic HTML in the document, page, and item views so later
    targeted repair always sees the same anchor. Original zero-based page
    indexes become one-based page_number values, including noncontiguous
    selections. Declared reading_order sorts elements; ties and unspecified
    orders retain source order. No boxes, page dimensions, model identity, or
    model-call evidence are synthesized. An actual empty pages list stays empty.
    """

    if (
        not isinstance(document_ir, Mapping)
        or not isinstance(document_ir.get("schema_version"), str)
        or document_ir["schema_version"] not in _SCHEMAS
    ):
        raise ParseOutputExportError("unsupported FileX Document IR schema_version")
    if document_ir.get("coordinate_system") != "pixel_top_left_xyxy":
        raise ParseOutputExportError(
            "Document IR requires pixel_top_left_xyxy coordinates"
        )
    markdown = normalize_structured_tables(_text(markdown, "markdown"))
    if (
        not _text(example_id, "example_id").strip()
        or not _text(pipeline_name, "pipeline_name").strip()
    ):
        raise ParseOutputExportError("example_id and pipeline_name must not be empty")
    raw_pages = document_ir.get("pages")
    if not isinstance(raw_pages, list):
        raise ParseOutputExportError("Document IR pages must be an array")

    pages: list[dict[str, Any]] = []
    layout_pages: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw_page in raw_pages:
        if not isinstance(raw_page, Mapping):
            raise ParseOutputExportError("Document IR page must be an object")
        page_index = _index(raw_page.get("page_index"), "page_index")
        if page_index in seen:
            raise ParseOutputExportError("Document IR contains duplicate page indexes")
        seen.add(page_index)
        width = _number(raw_page.get("width"), "page width")
        height = _number(raw_page.get("height"), "page height")
        if width < 1 or height < 1:
            raise ParseOutputExportError(
                "page width and height must be at least one pixel"
            )
        raw_elements = raw_page.get("elements")
        if not isinstance(raw_elements, list):
            raise ParseOutputExportError("Document IR elements must be an array")
        ordered: list[tuple[tuple[float, int], dict[str, Any]]] = []
        for position, element in enumerate(raw_elements):
            if not isinstance(element, Mapping):
                raise ParseOutputExportError("Document IR element must be an object")
            raw_text = _text(element.get("text"), "element text")
            raw_html = _text(element.get("html", ""), "element html")
            text = normalize_structured_tables(raw_text)
            html = normalize_structured_tables(raw_html)
            segment = _segment(element, width=width, height=height)
            order = element.get("reading_order")
            if order is not None:
                order = _index(order, "reading_order")
            raw_label = _text(element.get("type"), "element type")
            label_key = "-".join(raw_label.strip().lower().replace("_", " ").split())
            is_table = segment["label"] == "table"
            is_chart = label_key == "chart"
            if is_table and not html and "<table" in text.lower():
                html = text
            if is_chart and not html and "<table" in text.lower():
                html = text
            item = {
                "type": "table" if is_table else ("chart" if is_chart else "text"),
                "md": text or html,
                "html": html,
                "value": text or html,
                "bbox": dict(segment),
                "layout_segments": [segment],
                "reading_order": order,
            }
            element_id = element.get("id")
            if isinstance(element_id, str) and element_id.strip():
                item["id"] = element_id.strip()[:128]
            ordered.append(((math.inf if order is None else order, position), item))
        ordered.sort(key=lambda entry: entry[0])
        items = [item for _, item in ordered]
        page_text = "\n\n".join(item["value"] for item in items if item["value"])
        page_markdown = (
            markdown
            if len(raw_pages) == 1
            else normalize_structured_tables(
                _text(raw_page.get("markdown", page_text), "page markdown")
            )
        )
        orientation = raw_page.get("original_orientation_angle")
        if orientation is not None:
            orientation = _orientation(orientation)
        pages.append({"page_index": page_index, "markdown": page_markdown})
        layout_pages.append(
            {
                "page_number": page_index + 1,
                "width": width,
                "height": height,
                "md": page_markdown,
                "text": page_text,
                "items": items,
                **(
                    {"original_orientation_angle": orientation}
                    if orientation is not None
                    else {}
                ),
            }
        )
    pages.sort(key=lambda page: page["page_index"])
    layout_pages.sort(key=lambda page: page["page_number"])
    return {
        "task_type": "parse",
        "example_id": example_id,
        "pipeline_name": pipeline_name,
        "pages": pages,
        "layout_pages": layout_pages,
        "markdown": markdown,
    }


__all__ = [
    "ParseOutputExportError",
    "document_ir_to_parse_output",
    "normalize_structured_tables",
]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ParseOutputExportError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ParseOutputExportError("nonfinite JSON number")


def main(argv: list[str] | None = None) -> int:
    """Convert a bounded stdin envelope; no filesystem or dataset paths accepted."""

    parser = argparse.ArgumentParser(
        description=(
            "Read JSON {document_ir, markdown, example_id, pipeline_name?} from stdin "
            "and write the corresponding ParseOutput JSON to stdout. document_ir "
            "must be the original FileX pixel_top_left_xyxy IR. No files are opened."
        )
    )
    parser.parse_args(argv)
    try:
        request_bytes = sys.stdin.buffer.read(512 * 1024 * 1024 + 1)
        if len(request_bytes) > 512 * 1024 * 1024:
            raise ParseOutputExportError("export request exceeds 512 MiB")
        request = json.loads(
            request_bytes,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(request, dict) or set(request) - {
            "document_ir",
            "markdown",
            "example_id",
            "pipeline_name",
        }:
            raise ParseOutputExportError(
                "export request must contain the documented IR envelope"
            )
        output = document_ir_to_parse_output(
            request.get("document_ir"),
            markdown=request.get("markdown"),
            example_id=request.get("example_id"),
            pipeline_name=request.get("pipeline_name", "filex"),
        )
        print(
            json.dumps(
                output,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        return 0
    except (TypeError, ValueError, UnicodeError) as exc:
        print(f"ParseOutput export failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
