from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from document_parse_service.pdf.block_repair import (
    BlockRepairError,
    StructuredTable,
    repair_parse_output,
)


def test_structured_table_validates_and_renders_deterministic_html() -> None:
    table = StructuredTable.from_model_output(
        '```json\n{"columns":["Name","Value"],"rows":[["A & B","<10"]]}\n```',
        require_numeric=False,
    )

    assert table.to_html() == (
        "<table><thead><tr><th>Name</th><th>Value</th></tr></thead>"
        "<tbody><tr><td>A &amp; B</td><td>&lt;10</td></tr></tbody></table>"
    )


@pytest.mark.parametrize(
    "content,reason",
    [
        ('{"columns":["A"],"rows":[[1]]}', "filex_block_repair_shape_invalid"),
        (
            '{"columns":["A","B"],"rows":[["x","not numeric"]]}',
            "filex_chart_repair_numeric_value_missing",
        ),
        ('{"columns":["A","B"],"rows":[[1]]}', "filex_block_repair_row_invalid"),
    ],
)
def test_structured_table_rejects_unusable_model_output(content: str, reason: str) -> None:
    with pytest.raises(BlockRepairError, match=reason):
        StructuredTable.from_model_output(content, require_numeric=True)


@pytest.mark.asyncio
async def test_repair_parse_output_updates_only_targeted_table_block(tmp_path: Path) -> None:
    @dataclass
    class _Response:
        text: str

    class _Backend:
        async def transcribe(self, *_args, **_kwargs):
            return _Response(
                '{"columns":["Quarter","Revenue"],"rows":[["Q1","42"]]}'
            )

    def render(_source_path, **kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"png")
        return output

    prose = "The table reports quarterly revenue."
    layout = {
        "task_type": "parse",
        "layout_pages": [
            {
                "page_number": 1,
                "width": 100,
                "height": 100,
                "md": f"Before\n\n{prose}\n\nAfter",
                "items": [
                    {
                        "id": "table-1",
                        "type": "table",
                        "md": prose,
                        "html": "",
                        "value": prose,
                        "bbox": {"x": 10, "y": 20, "w": 70, "h": 40, "label": "table"},
                    }
                ],
            }
        ],
        "markdown": f"Before\n\n{prose}\n\nAfter",
    }
    report = {
        "tables": {
            "issues": [
                {
                    "reason": "filex_table_html_missing",
                    "page_number": 1,
                    "item_index": 0,
                    "block_id": "table-1",
                    "bbox": {"x": 10, "y": 20, "w": 70, "h": 40},
                }
            ]
        },
        "charts": {"issues": []},
    }

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document=layout["markdown"],
        layout=layout,
        quality_report=report,
        backend=_Backend(),
        crop_renderer=render,
    )

    assert result["attempted"] == 1
    assert result["failures"] == []
    assert result["repaired"] == [
        {
            "kind": "tables",
            "page_number": 1,
            "item_index": 0,
            "block_id": "table-1",
        }
    ]
    html = layout["layout_pages"][0]["items"][0]["html"]
    assert "<th>Quarter</th><th>Revenue</th>" in html
    assert "<td>Q1</td><td>42</td>" in html
    assert prose not in result["document"]
    assert result["layout"]["markdown"] == result["document"]


@pytest.mark.asyncio
async def test_chart_repair_creates_missing_picture_item_after_model_success(
    tmp_path: Path,
) -> None:
    @dataclass
    class _Response:
        text: str

    class _Backend:
        async def transcribe(self, *_args, **_kwargs):
            return _Response(
                '{"columns":["Series","Value"],"rows":[["Revenue","42"]]}'
            )

    def render(_source_path, **kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"png")
        return output

    layout = {
        "task_type": "parse",
        "layout_pages": [
            {
                "page_number": 1,
                "width": 100,
                "height": 100,
                "md": "Chart placeholder",
                "items": [],
            }
        ],
        "markdown": "Chart placeholder",
    }
    report = {
        "tables": {"issues": []},
        "charts": {
            "issues": [
                {
                    "reason": "filex_chart_content_unusable",
                    "page_number": 1,
                    "element_index": 0,
                    "block_id": "chart-1",
                    "bbox": {"x": 10, "y": 20, "w": 70, "h": 40},
                }
            ]
        },
    }

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document=layout["markdown"],
        layout=layout,
        quality_report=report,
        backend=_Backend(),
        crop_renderer=render,
    )

    assert result["failures"] == []
    item = result["layout"]["layout_pages"][0]["items"][0]
    assert item["id"] == "chart-1"
    assert item["bbox"]["label"] == "picture"
    assert "<td>Revenue</td><td>42</td>" in item["html"]
