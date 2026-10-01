from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from document_parse_service.pdf.block_repair import (
    BlockRepairError,
    StructuredTable,
    apply_structured_repair,
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
            '{"columns":["Quarter","Revenue"],'
            '"rows":[["Q1","not numeric"]]}',
            "filex_chart_repair_numeric_value_missing",
        ),
        ('{"columns":["A","B"],"rows":[[1]]}', "filex_block_repair_row_invalid"),
    ],
)
def test_structured_table_rejects_unusable_model_output(content: str, reason: str) -> None:
    with pytest.raises(BlockRepairError, match=reason):
        StructuredTable.from_model_output(content, require_numeric=True)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (
            '{"columns":["2023","2024"],"rows":[["10","20"]]}',
            "filex_chart_repair_labels_missing",
        ),
        (
            '{"columns":["Year","Revenue"],'
            '"rows":[["2024","10-20"]]}',
            "filex_chart_repair_range_invalid",
        ),
        (
            '{"columns":["Year","Revenue"],'
            '"rows":[["2024","about 42"]]}',
            "filex_chart_repair_narrative_value_invalid",
        ),
        (
            '{"columns":["Quarter","Revenue"],'
            '"rows":[["Q1","unknown"]]}',
            "filex_chart_repair_numeric_value_missing",
        ),
        (
            '{"columns":["Year","Value"],"rows":[["2023",""]]}',
            "filex_chart_repair_row_labels_missing",
        ),
    ],
)
def test_chart_table_rejects_unscorable_or_unbounded_values(
    content: str, reason: str
) -> None:
    with pytest.raises(BlockRepairError, match=reason):
        StructuredTable.from_model_output(content, require_numeric=True)


@pytest.mark.parametrize(
    "content",
    [
        '{"columns":["Month","Sales"],"rows":[["January","~42"]]}',
        '{"columns":["Year","Revenue","Profit"],'
        '"rows":[["2024","42","17"]]}',
        '{"columns":["Series","2023","2024"],'
        '"rows":[["Revenue","42","51"]]}',
        '{"columns":["2023","2024"],'
        '"rows":[["Revenue","42"]]}',
    ],
)
def test_chart_table_accepts_visible_flattened_headers_and_bounded_estimates(
    content: str,
) -> None:
    table = StructuredTable.from_model_output(
        content,
        require_numeric=True,
    )

    assert table.rows


def test_chart_table_allows_range_text_when_it_is_a_labeled_category() -> None:
    table = StructuredTable.from_model_output(
        '{"columns":["Period","Value"],'
        '"rows":[["2023-2024 cohort","42"]]}',
        require_numeric=True,
    )

    assert table.rows == (("2023-2024 cohort", "42"),)


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
                "text": f"Before {prose} After",
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
    html = result["layout"]["layout_pages"][0]["items"][0]["html"]
    assert "<th>Quarter</th><th>Revenue</th>" in html
    assert "<td>Q1</td><td>42</td>" in html
    assert prose not in result["document"]
    assert result["layout"]["markdown"] == result["document"]
    assert result["layout"]["layout_pages"][0]["text"] == f"Before {prose} After"
    assert layout["layout_pages"][0]["items"][0]["html"] == ""


def test_apply_repair_fails_closed_when_document_anchor_is_repeated() -> None:
    prose = "Repeated table description"
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "md": f"{prose}\n\n{prose}",
                "text": prose,
                "items": [
                    {
                        "id": "table-1",
                        "type": "table",
                        "md": prose,
                        "html": "",
                        "value": prose,
                        "bbox": {"x": 1, "y": 1, "w": 10, "h": 10, "label": "table"},
                    }
                ],
            }
        ],
        "markdown": f"{prose}\n\n{prose}",
    }
    original = layout.copy()

    with pytest.raises(BlockRepairError, match="filex_block_repair_document_anchor_ambiguous"):
        apply_structured_repair(
            document=layout["markdown"],
            layout=layout,
            issue={
                "reason": "filex_table_html_missing",
                "page_number": 1,
                "item_index": 0,
                "block_id": "table-1",
            },
            table=StructuredTable(("Name", "Value"), (("A", "1"),)),
        )

    assert layout == original


def test_apply_repair_requires_matching_page_item_and_block_identity() -> None:
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "md": "table prose",
                "items": [
                    {
                        "id": "table-real",
                        "type": "table",
                        "md": "table prose",
                        "html": "",
                        "value": "table prose",
                    }
                ],
            }
        ],
        "markdown": "table prose",
    }

    with pytest.raises(BlockRepairError, match="filex_block_repair_target_identity_mismatch"):
        apply_structured_repair(
            document="table prose",
            layout=layout,
            issue={
                "reason": "filex_table_html_missing",
                "page_number": 1,
                "item_index": 0,
                "block_id": "table-other",
            },
            table=StructuredTable(("Name", "Value"), (("A", "1"),)),
        )


def test_apply_repair_rejects_duplicate_block_identity_on_target_page() -> None:
    item = {
        "id": "table-1",
        "type": "table",
        "md": "table prose",
        "html": "",
        "value": "table prose",
    }
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "md": "table prose",
                "items": [item, {**item, "md": "other", "value": "other"}],
            }
        ],
        "markdown": "table prose",
    }

    with pytest.raises(BlockRepairError, match="filex_block_repair_target_identity_ambiguous"):
        apply_structured_repair(
            document="table prose",
            layout=layout,
            issue={
                "reason": "filex_table_html_missing",
                "page_number": 1,
                "item_index": 0,
                "block_id": "table-1",
            },
            table=StructuredTable(("Name", "Value"), (("A", "1"),)),
        )


@pytest.mark.asyncio
async def test_chart_repair_creates_missing_picture_item_after_model_success(
    tmp_path: Path,
) -> None:
    @dataclass
    class _Response:
        text: str

    class _Backend:
        async def transcribe(self, *_args, **kwargs):
            prompts.append(kwargs["options"]["prompt"])
            return _Response(
                '{"columns":["Month","Sales"],'
                '"rows":[["January","~42"]]}'
            )

    prompts = []
    render_options = []

    def render(_source_path, **kwargs):
        render_options.append(kwargs)
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
                "md": "Top block\n\nBottom block",
                "text": "Top block Bottom block",
                "items": [
                    {
                        "id": "bottom",
                        "type": "text",
                        "md": "Bottom block",
                        "html": "",
                        "value": "Bottom block",
                        "bbox": {"x": 0, "y": 80, "w": 80, "h": 10, "label": "text"},
                        "reading_order": 1,
                    },
                    {
                        "id": "top",
                        "type": "text",
                        "md": "Top block",
                        "html": "",
                        "value": "Top block",
                        "bbox": {"x": 0, "y": 0, "w": 80, "h": 10, "label": "text"},
                        "reading_order": 0,
                    },
                ],
            }
        ],
        "pages": [{"page_index": 0, "markdown": "Top block\n\nBottom block"}],
        "markdown": "Top block\n\nBottom block",
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
    items = result["layout"]["layout_pages"][0]["items"]
    item = items[1]
    assert [entry["id"] for entry in items] == ["top", "chart-1", "bottom"]
    assert item["id"] == "chart-1"
    assert item["type"] == "chart"
    assert item["bbox"]["label"] == "picture"
    assert "<td>January</td><td>~42</td>" in item["html"]
    assert [entry["reading_order"] for entry in items] == [0, 1, 2]
    assert result["document"].index(item["html"]) < result["document"].index("Bottom block")
    assert result["layout"]["layout_pages"][0]["text"] == "Top block Bottom block"
    assert render_options[0]["padding_ratio"] >= 0.20
    assert "bounded visual read" in prompts[0]
    assert "actual visible axis" in prompts[0]
    assert "best numeric estimate" not in prompts[0]


@pytest.mark.asyncio
async def test_missing_chart_without_unique_neighbor_anchor_fails_without_appending(
    tmp_path: Path,
) -> None:
    @dataclass
    class _Response:
        text: str

    class _Backend:
        async def transcribe(self, *_args, **_kwargs):
            return _Response(
                '{"columns":["Month","Sales"],'
                '"rows":[["January","~42"]]}'
            )

    def render(_source_path, **kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"png")
        return output

    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "width": 100,
                "height": 100,
                "md": "Unanchored page",
                "text": "Unanchored page",
                "items": [],
            }
        ],
        "markdown": "Unanchored page",
    }
    report = {
        "tables": {"issues": []},
        "charts": {
            "issues": [
                {
                    "reason": "filex_chart_content_unusable",
                    "page_number": 1,
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

    assert result["repaired"] == []
    assert result["failures"][0]["reason"] == "filex_block_repair_anchor_missing"
    assert result["document"] == "Unanchored page"
    assert result["layout"] == layout


@pytest.mark.asyncio
async def test_document_coverage_issue_is_reported_without_end_append(
    tmp_path: Path,
) -> None:
    table = "<table><tr><td>A</td><td>1</td></tr></table>"
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "width": 100,
                "height": 100,
                "md": "Document prose",
                "text": "Document prose",
                "items": [
                    {
                        "id": "table-1",
                        "type": "table",
                        "md": table,
                        "html": table,
                        "value": table,
                    }
                ],
            }
        ],
        "markdown": "Document prose",
    }

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document="Document prose",
        layout=layout,
        quality_report={
            "tables": {"issues": []},
            "charts": {"issues": []},
            "document": {
                "issues": [
                    {
                        "reason": "filex_document_table_coverage_incomplete",
                        "expected": 1,
                        "actual": 0,
                    }
                ]
            },
        },
        backend=object(),
    )

    assert result["document"] == "Document prose"
    assert result["layout"] == layout
    assert result["repaired"] == []
    assert result["failures"] == [
        {
            "kind": "document",
            "page_number": None,
            "item_index": None,
            "block_id": None,
            "reason": "filex_block_repair_document_anchor_required",
        }
    ]
