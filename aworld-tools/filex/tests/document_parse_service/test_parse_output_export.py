from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
SPEC = importlib.util.spec_from_file_location(
    "filex_parse_output_export_test",
    SOURCE_ROOT / "document_parse_service/parse_output_export.py",
)
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


def _real_provider_ir(*, text_label: str = "doc_title") -> dict:
    # Exercise the real provider's normalization of its native Paddle response.
    # Its existing test loader isolates optional model dependencies; no model runs.
    spec = importlib.util.spec_from_file_location(
        "filex_paddle_test_loader",
        Path(__file__).with_name("test_paddle_ocr_pdf_provider.py"),
    )
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    provider = helper._load_provider_module().PaddleOcrPdfProvider
    return provider._build_document_ir(
        [
            {
                "page_index": 2,
                "width": 100,
                "height": 200,
                "parsing_res_list": [
                    {
                        "block_label": "table",
                        "block_bbox": [10, 60, 90, 120],
                        "block_content": "<table><tr><td>Value</td></tr></table>",
                        "block_order": 2,
                    },
                    {
                        "block_label": text_label,
                        "block_bbox": [10, 10, 90, 40],
                        "block_content": "Heading",
                        "block_order": 1,
                    },
                ],
            }
        ]
    )


def _export(ir: dict, *, markdown: str = "# Heading\r\n\n原文  \n") -> dict:
    return EXPORTER.document_ir_to_parse_output(
        ir, markdown=markdown, example_id="source-page-3"
    )


def test_exports_real_provider_ir_with_original_page_order_and_html() -> None:
    ir = _real_provider_ir()
    ir["pages"][0]["spans"] = [
        {"text": "text layer only", "x": 1, "y": 2, "font_size": 12}
    ]
    original = copy.deepcopy(ir)
    output = _export(ir)
    assert ir == original
    assert output["markdown"] == "# Heading\r\n\n原文  \n"
    assert output["pages"][0]["page_index"] == 2
    page = output["layout_pages"][0]
    assert page["page_number"] == 3
    assert [item["reading_order"] for item in page["items"]] == [1, 2]
    assert [item["bbox"]["label"] for item in page["items"]] == ["title", "table"]
    table = page["items"][1]
    assert table["html"] == table["md"] == "<table><tr><td>Value</td></tr></table>"
    assert table["bbox"] == {
        "x": 10.0,
        "y": 60.0,
        "w": 80.0,
        "h": 60.0,
        "label": "table",
    }
    assert len(page["items"]) == 2  # Text-layer spans do not invent new geometry.
    assert "confidence" not in table["bbox"]  # No fabricated provider confidence.
    assert "model" not in output


def test_paddle_ocr_label_exports_text_without_changing_parser_geometry() -> None:
    ir = _real_provider_ir(text_label="ocr")
    original = copy.deepcopy(ir)
    item = _export(ir)["layout_pages"][0]["items"][0]
    assert ir == original
    assert item["type"] == "text"
    assert item["md"] == "Heading"
    assert item["bbox"] == {
        "x": 10.0, "y": 10.0, "w": 80.0, "h": 30.0, "label": "text"
    }


def test_noncontiguous_pages_and_missing_reading_order_keep_source_identity() -> None:
    ir = _real_provider_ir()
    page = ir["pages"][0]
    page["page_index"] = 4
    for element in page["elements"]:
        element["reading_order"] = None
    earlier = copy.deepcopy(page)
    earlier["page_index"] = 1
    ir["pages"].append(earlier)
    output = _export(ir)
    assert [page["page_number"] for page in output["layout_pages"]] == [2, 5]
    assert [page["page_index"] for page in output["pages"]] == [1, 4]
    assert [item["type"] for item in output["layout_pages"][0]["items"]] == [
        "table",
        "text",
    ]
    assert all(
        item["reading_order"] is None for item in output["layout_pages"][0]["items"]
    )


@pytest.mark.parametrize("schema", ["filex-document-ir-v1", "filex-document-ir-v2"])
def test_empty_real_pages_and_items_remain_empty(schema: str) -> None:
    ir = {
        "schema_version": schema,
        "coordinate_system": "pixel_top_left_xyxy",
        "pages": [],
    }
    output = _export(ir, markdown="")
    assert output["pages"] == output["layout_pages"] == []
    assert output["markdown"] == ""
    ir["pages"] = [{"page_index": 2, "width": 100, "height": 200, "elements": []}]
    output = _export(ir, markdown="")
    assert output["layout_pages"][0]["page_number"] == 3
    assert output["layout_pages"][0]["items"] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda ir: ir.update(schema_version=[]),
        lambda ir: ir.update(coordinate_system="normalized_xyxy"),
        lambda ir: ir.update(pages={}),
        lambda ir: ir["pages"].append(copy.deepcopy(ir["pages"][0])),
        lambda ir: ir["pages"][0].update(page_index=True),
        lambda ir: ir["pages"][0].update(width=None),
        lambda ir: ir["pages"][0].update(height=0),
        lambda ir: ir["pages"][0].update(elements=None),
        lambda ir: ir["pages"][0]["elements"][0].update(bbox=None),
        lambda ir: ir["pages"][0]["elements"][0].update(bbox=[0, 0, 101, 100]),
        lambda ir: ir["pages"][0]["elements"][0].update(bbox=[0, 0, float("nan"), 100]),
        lambda ir: ir["pages"][0]["elements"][0].update(type="unknown"),
        lambda ir: ir["pages"][0]["elements"][0].update(text=None),
        lambda ir: ir["pages"][0]["elements"][0].update(reading_order=1.5),
        lambda ir: ir["pages"][0]["elements"][0].update(confidence=1.1),
    ],
)
def test_invalid_ir_is_rejected_without_guessed_geometry(mutate) -> None:
    ir = _real_provider_ir()
    mutate(ir)
    with pytest.raises(EXPORTER.ParseOutputExportError):
        _export(ir)


def test_wheel_module_cli_consumes_only_explicit_ir_stdin() -> None:
    request = {
        "document_ir": _real_provider_ir(),
        "markdown": "public",
        "example_id": "sample",
    }
    process = subprocess.run(
        [sys.executable, "-m", "document_parse_service.parse_output_export"],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(SOURCE_ROOT)},
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["layout_pages"][0]["page_number"] == 3
    request["ground_truth_path"] = "/must/not/be/read"
    invalid = subprocess.run(
        [sys.executable, "-m", "document_parse_service.parse_output_export"],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(SOURCE_ROOT)},
    )
    assert invalid.returncode == 2
    assert invalid.stdout == ""
