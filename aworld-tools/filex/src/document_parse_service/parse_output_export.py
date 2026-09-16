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
import sys
from collections.abc import Mapping
from typing import Any


class ParseOutputExportError(ValueError):
    """The source IR cannot be represented without guessing its geometry."""


_SCHEMAS = {"filex-document-ir-v1", "filex-document-ir-v2"}
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

    The full Markdown is preserved exactly. Original zero-based page indexes
    become one-based page_number values, including noncontiguous selections.
    Declared reading_order sorts elements; ties and unspecified orders retain
    source order. No boxes, page dimensions, model identity, or model-call
    evidence are synthesized. An actual empty pages list stays empty.
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
    _text(markdown, "markdown")
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
            text = _text(element.get("text"), "element text")
            html = _text(element.get("html", ""), "element html")
            segment = _segment(element, width=width, height=height)
            order = element.get("reading_order")
            if order is not None:
                order = _index(order, "reading_order")
            is_table = segment["label"] == "table"
            if is_table and not html and "<table" in text.lower():
                html = text
            item = {
                "type": "table" if is_table else "text",
                "md": text or html,
                "html": html,
                "value": text or html,
                "bbox": dict(segment),
                "layout_segments": [segment],
                "reading_order": order,
            }
            ordered.append(((math.inf if order is None else order, position), item))
        ordered.sort(key=lambda entry: entry[0])
        items = [item for _, item in ordered]
        page_text = "\n\n".join(item["value"] for item in items if item["value"])
        page_markdown = (
            markdown
            if len(raw_pages) == 1
            else _text(raw_page.get("markdown", page_text), "page markdown")
        )
        pages.append({"page_index": page_index, "markdown": page_markdown})
        layout_pages.append(
            {
                "page_number": page_index + 1,
                "width": width,
                "height": height,
                "md": page_markdown,
                "text": page_text,
                "items": items,
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


__all__ = ["ParseOutputExportError", "document_ir_to_parse_output"]


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
