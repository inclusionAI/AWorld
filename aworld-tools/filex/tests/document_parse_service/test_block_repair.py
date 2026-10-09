from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from document_parse_service.pdf.block_repair import (
    BlockRepairError,
    StructuredTable,
    _estimate_evidence_declared,
    _validated_model_table,
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


def test_structured_table_accepts_exactly_one_json_object_with_bounded_boilerplate() -> (
    None
):
    table = StructuredTable.from_model_output(
        "Here is the requested JSON.\n"
        "```json\n"
        '{"columns":["Name","Value"],"rows":[["A","1"]]}\n'
        "```\n"
        "End of response.",
        require_numeric=False,
    )

    assert table.to_html() == (
        "<table><thead><tr><th>Name</th><th>Value</th></tr></thead>"
        "<tbody><tr><td>A</td><td>1</td></tr></tbody></table>"
    )


def test_structured_table_rejects_multiple_json_objects_or_unbounded_boilerplate() -> (
    None
):
    payload = '{"columns":["Name","Value"],"rows":[["A","1"]]}'

    with pytest.raises(BlockRepairError, match="filex_block_repair_invalid_json"):
        StructuredTable.from_model_output(
            f"{payload}\n{payload}",
            require_numeric=False,
        )
    with pytest.raises(BlockRepairError, match="filex_block_repair_invalid_json"):
        StructuredTable.from_model_output(
            "x" * 4097 + payload,
            require_numeric=False,
        )


def test_structured_table_preserves_caption_headers_and_spans() -> None:
    table = StructuredTable.from_model_output(
        """{
          "caption":"Quarterly revenue",
          "notes":["Source: audited filing","Unit: USD millions"],
          "columns":[
            {"text":"Region","rowspan":2,"header":true},
            {"text":"Revenue","colspan":2,"header":true}
          ],
          "rows":[
            [{"text":"Q1","header":true},{"text":"Q2","header":true}],
            ["North","10","20"]
          ]
        }""",
        require_numeric=False,
    )

    assert table.to_html() == (
        "<table><caption>Quarterly revenue</caption><thead><tr>"
        '<th rowspan="2">Region</th><th colspan="2">Revenue</th>'
        "</tr></thead><tbody><tr><th>Q1</th><th>Q2</th></tr>"
        "<tr><td>North</td><td>10</td><td>20</td></tr></tbody></table>"
        '<div class="filex-structured-notes"><p>Source: audited filing</p>'
        "<p>Unit: USD millions</p></div>"
    )


def test_direct_string_grid_construction_remains_compatible() -> None:
    table = StructuredTable(("Name", "Value"), (("A", "1"),))

    assert table.columns == ("Name", "Value")
    assert table.to_html() == (
        "<table><thead><tr><th>Name</th><th>Value</th></tr></thead>"
        "<tbody><tr><td>A</td><td>1</td></tr></tbody></table>"
    )


@pytest.mark.parametrize(
    "content,reason",
    [
        (
            '{"labels":["A"],"estimated":false,"value_columns":[0],'
            '"columns":["A"],"rows":[[1]]}',
            "filex_block_repair_shape_invalid",
        ),
        (
            '{"labels":["Quarter","Revenue","Q1"],"estimated":false,'
            '"value_columns":[1],"columns":["Quarter","Revenue"],'
            '"rows":[["Q1","not numeric"]]}',
            "filex_chart_repair_numeric_value_missing",
        ),
        (
            '{"labels":["A","B"],"estimated":false,"value_columns":[1],'
            '"columns":["A","B"],"rows":[[1]]}',
            "filex_block_repair_row_invalid",
        ),
    ],
)
def test_structured_table_rejects_unusable_model_output(
    content: str, reason: str
) -> None:
    with pytest.raises(BlockRepairError, match=reason):
        StructuredTable.from_model_output(content, require_numeric=True)


@pytest.mark.parametrize(
    "content",
    [
        '{"estimated":false,"value_columns":[1],"columns":["Year","Value"],'
        '"rows":[["2024","42"]]}',
        '{"labels":["Year","Value","2024"],"value_columns":[1],'
        '"columns":["Year","Value"],"rows":[["2024","42"]]}',
        '{"labels":["Year","Value","2024"],"estimated":false,'
        '"columns":["Year","Value"],"rows":[["2024","42"]]}',
    ],
)
def test_chart_table_requires_explicit_evidence_contract(content: str) -> None:
    with pytest.raises(BlockRepairError, match="filex_block_repair_schema_invalid"):
        StructuredTable.from_model_output(content, require_numeric=True)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (
            '{"labels":["2023","2024"],"estimated":false,"value_columns":[1],'
            '"columns":["2023","2024"],"rows":[["10","20"]]}',
            "filex_chart_repair_labels_missing",
        ),
        (
            '{"labels":["Year","Revenue","2024"],"estimated":false,'
            '"value_columns":[1],"columns":["Year","Revenue"],'
            '"rows":[["2024","10-20"]]}',
            "filex_chart_repair_range_invalid",
        ),
        (
            '{"labels":["Year","Revenue","2024"],"estimated":false,'
            '"value_columns":[1],"columns":["Year","Revenue"],'
            '"rows":[["2024","about 42"]]}',
            "filex_chart_repair_narrative_value_invalid",
        ),
        (
            '{"labels":["Quarter","Revenue","Q1"],"estimated":false,'
            '"value_columns":[1],"columns":["Quarter","Revenue"],'
            '"rows":[["Q1","unknown"]]}',
            "filex_chart_repair_numeric_value_missing",
        ),
        (
            '{"labels":["Year","Value","2021"],"estimated":false,'
            '"value_columns":[1],"columns":["Year","Value"],'
            '"rows":[["2021","garbage"]]}',
            "filex_chart_repair_numeric_value_missing",
        ),
        (
            '{"labels":["Year","Value","2023"],"estimated":false,'
            '"value_columns":[1],"columns":["Year","Value"],'
            '"rows":[["2023",""]]}',
            "filex_chart_repair_numeric_value_missing",
        ),
        (
            '{"labels":["Month","Sales","January"],"estimated":false,'
            '"value_columns":[1],"columns":["Month","Sales"],'
            '"rows":[["January","~42"]]}',
            "filex_chart_repair_estimated_value_unverified",
        ),
        (
            '{"labels":["Month","Sales","January"],"estimated":true,'
            '"value_columns":[1],'
            '"columns":["Month","Sales"],"rows":[["January","42"]]}',
            "filex_chart_repair_estimated_value_unverified",
        ),
        (
            '{"labels":["Month","Sales ($)","January"],"estimated":true,'
            '"value_columns":[1],"columns":["Month","Sales ($)"],'
            '"rows":[["January","≈$42"]]}',
            "filex_chart_repair_estimated_currency_inline",
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
        '{"labels":["Month","Sales","January"],"estimated":false,'
        '"value_columns":[1],"columns":["Month","Sales"],'
        '"rows":[["January","42"]]}',
        '{"labels":["Year","Revenue","Profit","2024"],"estimated":false,'
        '"value_columns":[1,2],"columns":["Year","Revenue","Profit"],'
        '"rows":[["2024","42","17"]]}',
        '{"labels":["Series","2023","2024","Revenue"],"estimated":false,'
        '"value_columns":[1,2],"columns":["Series","2023","2024"],'
        '"rows":[["Revenue","42","51"]]}',
        '{"labels":["2023","2024","Revenue"],"estimated":false,'
        '"value_columns":[1],"columns":["2023","2024"],'
        '"rows":[["Revenue","42"]]}',
        '{"labels":["Period","Change","2024"],"estimated":false,'
        '"value_columns":[1],"columns":["Period","Change"],'
        '"rows":[["2024","+15.2%*"]]}',
        '{"labels":["Period","Change","2024"],"estimated":false,'
        '"value_columns":[1],"columns":["Period","Change"],'
        '"rows":[["2024","15.2%¹"]]}',
        '{"labels":["Month","Sales","January"],"estimated":true,'
        '"value_columns":[1],"columns":["Month","Sales"],'
        '"rows":[["January","~42"]]}',
        '{"labels":["Year","Revenue","Profit","2024"],"estimated":true,'
        '"value_columns":[1,2],"columns":["Year","Revenue","Profit"],'
        '"rows":[["2024","42","≈17"]]}',
        '{"labels":["Year","Value","2023","2024"],"estimated":false,'
        '"value_columns":[1],"columns":["Year","Value"],'
        '"rows":[["2023","N/A"],["2024","42"]]}',
    ],
)
def test_chart_table_accepts_visible_labels_and_supported_measure_values(
    content: str,
) -> None:
    table = StructuredTable.from_model_output(
        content,
        require_numeric=True,
    )

    assert table.rows


def test_chart_table_preserves_visible_caption_and_declared_labels() -> None:
    table = StructuredTable.from_model_output(
        '{"caption":"Annual results","labels":["Annual results","Year",'
        '"Revenue","2024"],"estimated":false,"value_columns":[1],'
        '"columns":["Year","Revenue"],"rows":[["2024","42"]]}',
        require_numeric=True,
    )

    assert table.labels == ("Annual results", "Year", "Revenue", "2024")
    assert table.to_html().startswith("<table><caption>Annual results</caption><thead>")


def test_chart_retry_cannot_launder_estimate_evidence() -> None:
    unmarked_estimate = (
        '{"labels":["Year","Value","2024"],"estimated":true,'
        '"value_columns":[1],"columns":["Year","Value"],'
        '"rows":[["2024","42"]]}'
    )
    laundered_exact = (
        '{"labels":["Year","Value","2024"],"estimated":false,'
        '"value_columns":[1],"columns":["Year","Value"],'
        '"rows":[["2024","42"]]}'
    )
    corrected = (
        '{"labels":["Year","Value","2024"],"estimated":true,'
        '"value_columns":[1],"columns":["Year","Value"],'
        '"rows":[["2024","≈42"]]}'
    )
    structured_marked_estimate = (
        '{"labels":["Year","Value","2024"],"estimated":false,'
        '"value_columns":[1],"columns":["Year","Value"],'
        '"rows":[["2024",{"text":"~42"}]]}'
    )

    assert _estimate_evidence_declared(unmarked_estimate)
    assert _estimate_evidence_declared(structured_marked_estimate)
    with pytest.raises(
        BlockRepairError,
        match="filex_chart_repair_estimated_value_unverified",
    ):
        _validated_model_table(
            laundered_exact,
            require_numeric=True,
            estimate_evidence_seen=True,
        )
    assert (
        _validated_model_table(
            corrected,
            require_numeric=True,
            estimate_evidence_seen=True,
        ).estimated
        is True
    )


def test_chart_table_rejects_declared_label_that_was_not_preserved() -> None:
    with pytest.raises(BlockRepairError, match="filex_chart_repair_label_missing"):
        StructuredTable.from_model_output(
            '{"caption":"Annual results","labels":["Profit"],'
            '"estimated":false,"value_columns":[1],'
            '"columns":["Year","Revenue"],'
            '"rows":[["2024","42"]]}',
            require_numeric=True,
        )


def test_chart_table_allows_range_text_when_it_is_a_labeled_category() -> None:
    table = StructuredTable.from_model_output(
        '{"labels":["Period","Value","2023-2024 cohort"],"estimated":false,'
        '"value_columns":[1],"columns":["Period","Value"],'
        '"rows":[["2023-2024 cohort","42"]]}',
        require_numeric=True,
    )

    assert tuple(cell.text for cell in table.rows[0]) == ("2023-2024 cohort", "42")


def _table_repair_fixture(
    table_prose: list[str],
) -> tuple[dict[str, object], dict[str, object]]:
    items: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    for index, prose in enumerate(table_prose):
        block_id = f"table-{index}"
        bbox = {"x": 5, "y": 5 + index * 20, "w": 80, "h": 15}
        items.append(
            {
                "id": block_id,
                "type": "table",
                "md": prose,
                "html": "",
                "value": prose,
                "bbox": {**bbox, "label": "table"},
            }
        )
        issues.append(
            {
                "reason": "filex_structured_block_meta_prose",
                "page_number": 1,
                "item_index": index,
                "block_id": block_id,
                "bbox": bbox,
            }
        )
    markdown = "\n\n".join(table_prose)
    return (
        {
            "task_type": "parse",
            "layout_pages": [
                {
                    "page_number": 1,
                    "width": 100,
                    "height": 100,
                    "md": markdown,
                    "text": markdown,
                    "items": items,
                }
            ],
            "markdown": markdown,
        },
        {
            "tables": {"issues": issues},
            "charts": {"issues": []},
            "document": {"issues": []},
        },
    )


@pytest.mark.asyncio
async def test_repair_parse_output_updates_only_targeted_table_block(
    tmp_path: Path,
) -> None:
    @dataclass
    class _Response:
        text: str

    class _Backend:
        async def transcribe(self, *_args, **_kwargs):
            return _Response('{"columns":["Quarter","Revenue"],"rows":[["Q1","42"]]}')

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
    assert result["layout"]["layout_pages"][0]["text"] == html
    assert layout["layout_pages"][0]["items"][0]["html"] == ""


@pytest.mark.asyncio
async def test_table_repair_prompt_defines_physical_grid_and_retry_names_reason(
    tmp_path: Path,
) -> None:
    @dataclass
    class _Response:
        text: str
        metadata: dict[str, str] | None = None

    class _Backend:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def transcribe(self, *_args, **kwargs):
            self.prompts.append(kwargs["options"]["prompt"])
            if len(self.prompts) == 1:
                return _Response(
                    '{"columns":[{"text":"Region","rowspan":2},'
                    '{"text":"Revenue","colspan":2}],'
                    '"rows":[[{"text":"Q1","header":true}],'
                    '["North","10","20"]]}'
                )
            return _Response(
                '{"columns":[{"text":"Region","rowspan":2},'
                '{"text":"Revenue","colspan":2}],'
                '"rows":[[{"text":"Q1","header":true},'
                '{"text":"Q2","header":true}],["North","10","20"]]}'
            )

    def render(_source_path, **kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"png")
        return output

    layout, report = _table_repair_fixture(["Native table prose"])
    backend = _Backend()

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document=layout["markdown"],
        layout=layout,
        quality_report=report,
        backend=backend,
        crop_renderer=render,
    )

    assert result["failures"] == []
    assert len(backend.prompts) == 2
    assert "first physical header row" in backend.prompts[0]
    assert "sum of its colspan" in backend.prompts[0]
    assert "active rowspans" in backend.prompts[0]
    assert "filex_block_repair_row_invalid" in backend.prompts[1]


@pytest.mark.asyncio
async def test_table_repair_length_finish_gets_one_bounded_token_increase(
    tmp_path: Path,
) -> None:
    @dataclass
    class _Response:
        text: str
        metadata: dict[str, str]

    class _Backend:
        def __init__(self) -> None:
            self.options: list[dict[str, object]] = []

        async def transcribe(self, *_args, **kwargs):
            self.options.append(kwargs["options"])
            if len(self.options) == 1:
                return _Response(
                    '{"columns":["Name","Value"]',
                    {"finish_reason": "length"},
                )
            return _Response(
                '{"columns":["Name","Value"],"rows":[["A","1"]]}',
                {"finish_reason": "stop"},
            )

    def render(_source_path, **kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"png")
        return output

    layout, report = _table_repair_fixture(["Native table prose"])
    backend = _Backend()

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document=layout["markdown"],
        layout=layout,
        quality_report=report,
        backend=backend,
        crop_renderer=render,
    )

    assert result["failures"] == []
    assert len(backend.options) == 2
    assert backend.options[0]["max_tokens"] == 4096
    assert backend.options[1]["max_tokens"] == 8192
    assert "filex_block_repair_output_truncated" in str(backend.options[1]["prompt"])


@pytest.mark.asyncio
async def test_multi_table_repair_reports_all_successes(tmp_path: Path) -> None:
    @dataclass
    class _Response:
        text: str

    class _Backend:
        def __init__(self) -> None:
            self.calls = 0

        async def transcribe(self, *_args, **_kwargs):
            self.calls += 1
            return _Response(
                '{"columns":["Name","Value"],"rows":[["Row '
                + str(self.calls)
                + '","1"]]}'
            )

    def render(_source_path, **kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"png")
        return output

    layout, report = _table_repair_fixture(
        ["First native table", "Second native table"]
    )
    backend = _Backend()

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document=layout["markdown"],
        layout=layout,
        quality_report=report,
        backend=backend,
        crop_renderer=render,
    )

    assert result["attempted"] == 2
    assert result["remaining"] == 0
    assert result["failures"] == []
    assert [item["block_id"] for item in result["repaired"]] == [
        "table-0",
        "table-1",
    ]
    assert backend.calls == 2


@pytest.mark.asyncio
async def test_multi_table_repair_retains_mixed_failure_summary(tmp_path: Path) -> None:
    @dataclass
    class _Response:
        text: str

    class _Backend:
        def __init__(self) -> None:
            self.calls = 0

        async def transcribe(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _Response('{"columns":["Name","Value"],"rows":[["A","1"]]}')
            return _Response("not json")

    def render(_source_path, **kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"png")
        return output

    layout, report = _table_repair_fixture(
        ["First native table", "Second native table"]
    )
    backend = _Backend()

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document=layout["markdown"],
        layout=layout,
        quality_report=report,
        backend=backend,
        crop_renderer=render,
    )

    assert result["attempted"] == 2
    assert result["remaining"] == 0
    assert [item["block_id"] for item in result["repaired"]] == ["table-0"]
    assert result["failures"] == [
        {
            "kind": "tables",
            "page_number": 1,
            "item_index": 1,
            "block_id": "table-1",
            "reason": "filex_block_repair_invalid_json",
        }
    ]
    assert backend.calls == 3


@pytest.mark.asyncio
async def test_repair_parse_output_returns_partial_before_total_deadline(
    tmp_path: Path,
) -> None:
    class _SlowBackend:
        async def transcribe(self, *_args, **_kwargs):
            await asyncio.sleep(10)

    def render(_source_path, **kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"png")
        return output

    items = []
    issues = []
    for index in range(2):
        block_id = f"table-{index}"
        prose = f"Table {index}"
        items.append(
            {
                "id": block_id,
                "type": "table",
                "md": prose,
                "html": "",
                "value": prose,
                "bbox": {
                    "x": 0,
                    "y": index * 20,
                    "w": 50,
                    "h": 10,
                    "label": "table",
                },
            }
        )
        issues.append(
            {
                "reason": "filex_table_html_missing",
                "page_number": 1,
                "item_index": index,
                "block_id": block_id,
                "bbox": {"x": 0, "y": index * 20, "w": 50, "h": 10},
            }
        )
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "width": 100,
                "height": 100,
                "md": "Table 0\n\nTable 1",
                "text": "Table 0 Table 1",
                "items": items,
            }
        ],
        "markdown": "Table 0\n\nTable 1",
    }

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document=layout["markdown"],
        layout=layout,
        quality_report={"tables": {"issues": issues}, "charts": {"issues": []}},
        backend=_SlowBackend(),
        crop_renderer=render,
        max_blocks=2,
        total_timeout_seconds=2,
    )

    assert result["attempted"] == 1
    assert result["remaining"] == 1
    assert result["repaired"] == []
    assert result["failures"][0]["reason"] == "filex_block_repair_backend_timeout"


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

    with pytest.raises(
        BlockRepairError, match="filex_block_repair_document_anchor_ambiguous"
    ):
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


def test_apply_repair_disambiguates_repeated_target_with_unique_neighbor_context() -> (
    None
):
    prose = "Repeated table description"
    page_markdown = f"Unique before\n\n{prose}\n\nUnique after\n\n{prose}"
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "md": page_markdown,
                "text": page_markdown,
                "items": [
                    {
                        "id": "before",
                        "type": "text",
                        "md": "Unique before",
                        "html": "",
                        "value": "Unique before",
                    },
                    {
                        "id": "table-target",
                        "type": "table",
                        "md": prose,
                        "html": "",
                        "value": prose,
                    },
                    {
                        "id": "after",
                        "type": "text",
                        "md": "Unique after",
                        "html": "",
                        "value": "Unique after",
                    },
                    {
                        "id": "duplicate-prose",
                        "type": "text",
                        "md": prose,
                        "html": "",
                        "value": prose,
                    },
                ],
            }
        ],
        "pages": [{"page_index": 0, "markdown": page_markdown}],
        "markdown": page_markdown,
    }
    replacement = StructuredTable(("Name", "Value"), (("A", "1"),)).to_html()

    document, repaired = apply_structured_repair(
        document=page_markdown,
        layout=layout,
        issue={
            "reason": "filex_structured_block_meta_prose",
            "page_number": 1,
            "item_index": 99,
            "block_id": "table-target",
        },
        table=StructuredTable(("Name", "Value"), (("A", "1"),)),
    )

    expected = f"Unique before\n\n{replacement}\n\nUnique after\n\n{prose}"
    assert document == expected
    assert repaired["markdown"] == expected
    assert repaired["layout_pages"][0]["md"] == expected
    assert repaired["pages"][0]["markdown"] == expected
    assert repaired["layout_pages"][0]["items"][1]["html"] == replacement


def test_apply_repair_uses_unique_visible_anchor_across_formatting_drift() -> None:
    item_prose = (
        "<table><tr><td>Smoking among 15-year-olds in 2014 across "
        "European countries</td></tr></table>"
    )
    formatted_prose = (
        "Smoking among 15-**year**-olds in **2014** across European countries"
    )
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "md": formatted_prose,
                "text": formatted_prose,
                "items": [
                    {
                        "id": "chart-1",
                        "type": "chart",
                        "md": item_prose,
                        "html": item_prose,
                        "value": item_prose,
                        "bbox": {
                            "x": 1,
                            "y": 1,
                            "w": 10,
                            "h": 10,
                            "label": "picture",
                        },
                    }
                ],
            }
        ],
        "pages": [{"page_index": 0, "markdown": formatted_prose}],
        "markdown": formatted_prose,
    }
    replacement = StructuredTable(
        ("Country", "Value"),
        (("A", "1"),),
        value_columns=(1,),
    )

    document, repaired = apply_structured_repair(
        document=formatted_prose,
        layout=layout,
        issue={
            "reason": "filex_chart_content_unusable",
            "page_number": 1,
            "item_index": 0,
            "block_id": "chart-1",
        },
        table=replacement,
    )

    assert document == replacement.to_html()
    assert repaired["layout_pages"][0]["md"] == replacement.to_html()
    assert repaired["pages"][0]["markdown"] == replacement.to_html()
    assert repaired["layout_pages"][0]["items"][0]["filex_chart_value_columns"] == [1]


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

    with pytest.raises(
        BlockRepairError, match="filex_block_repair_target_identity_mismatch"
    ):
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

    with pytest.raises(
        BlockRepairError, match="filex_block_repair_target_identity_ambiguous"
    ):
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
                '{"caption":"Monthly sales","labels":["Monthly sales",'
                '"Month","Sales","January"],"estimated":false,'
                '"value_columns":[1],'
                '"columns":["Month","Sales"],"rows":[["January","42"]]}'
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
                        "id": "top",
                        "type": "text",
                        "md": "Top block",
                        "html": "",
                        "value": "Top block",
                        "bbox": {"x": 0, "y": 0, "w": 80, "h": 10, "label": "text"},
                        "reading_order": 0,
                    },
                    {
                        "id": "bottom",
                        "type": "text",
                        "md": "Bottom block",
                        "html": "",
                        "value": "Bottom block",
                        "bbox": {"x": 0, "y": 80, "w": 80, "h": 10, "label": "text"},
                        "reading_order": 1,
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
    assert "<caption>Monthly sales</caption>" in item["html"]
    assert "<td>January</td><td>42</td>" in item["html"]
    assert [entry["reading_order"] for entry in items if "reading_order" in entry] == [
        0,
        1,
    ]
    assert result["document"].index(item["html"]) < result["document"].index(
        "Bottom block"
    )
    assert result["layout"]["layout_pages"][0]["text"] == (
        f"Top block\n\n{item['html']}\n\nBottom block"
    )
    assert render_options[0]["padding_ratio"] >= 0.20
    assert "every visible axis" in prompts[0]
    assert "visible labelled axis and tick scale" in prompts[0]
    assert "prefix that cell with ≈" in prompts[0]
    assert "never extrapolate beyond visible ticks" in prompts[0]


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
                '{"caption":"","labels":["Month","Sales","January"],'
                '"estimated":false,"value_columns":[1],'
                '"columns":["Month","Sales"],'
                '"rows":[["January","42"]]}'
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
async def test_multiple_chart_repairs_relocate_by_id_without_reordering_existing_items(
    tmp_path: Path,
) -> None:
    @dataclass
    class _Response:
        text: str

    class _Backend:
        def __init__(self) -> None:
            self.calls = 0

        async def transcribe(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _Response(
                    '{"caption":"New chart","labels":["New chart","Year",'
                    '"Value"],"estimated":false,"value_columns":[1],'
                    '"columns":["Year","Value"],'
                    '"rows":[["2024","10"]]}'
                )
            return _Response(
                '{"caption":"Existing chart","labels":["Existing chart",'
                '"Year","Value"],"estimated":false,"value_columns":[1],'
                '"columns":["Year","Value"],"rows":[["2024","20"]]}'
            )

    def render(_source_path, **kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"png")
        return output

    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "width": 200,
                "height": 200,
                "md": "Right column\n\nLeft column\n\nExisting chart prose",
                "text": "Right column Left column Existing chart prose",
                "items": [
                    {
                        "id": "right-column",
                        "type": "text",
                        "md": "Right column",
                        "html": "",
                        "value": "Right column",
                        "bbox": {"x": 110, "y": 5, "w": 80, "h": 20, "label": "text"},
                        "reading_order": 7,
                    },
                    {
                        "id": "left-column",
                        "type": "text",
                        "md": "Left column",
                        "html": "",
                        "value": "Left column",
                        # Geometry sorts before right-column, but provider order does not.
                        "bbox": {"x": 5, "y": 5, "w": 80, "h": 20, "label": "text"},
                        "reading_order": 9,
                    },
                    {
                        "id": "chart-existing",
                        "type": "chart",
                        "md": "Existing chart prose",
                        "html": "",
                        "value": "Existing chart prose",
                        "bbox": {
                            "x": 5,
                            "y": 120,
                            "w": 180,
                            "h": 50,
                            "label": "picture",
                        },
                        "reading_order": 11,
                    },
                ],
            }
        ],
        "pages": [
            {
                "page_index": 0,
                "markdown": "Right column\n\nLeft column\n\nExisting chart prose",
            }
        ],
        "markdown": "Right column\n\nLeft column\n\nExisting chart prose",
    }
    report = {
        "tables": {"issues": []},
        "charts": {
            "issues": [
                {
                    "reason": "filex_chart_content_unusable",
                    "page_number": 1,
                    "block_id": "chart-new",
                    "bbox": {"x": 5, "y": 60, "w": 180, "h": 40},
                },
                {
                    "reason": "filex_chart_content_unusable",
                    "page_number": 1,
                    # This index becomes stale after chart-new is inserted.
                    "item_index": 2,
                    "block_id": "chart-existing",
                    "bbox": {"x": 5, "y": 120, "w": 180, "h": 50},
                },
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
    assert len(result["repaired"]) == 2
    items = result["layout"]["layout_pages"][0]["items"]
    assert [item["id"] for item in items] == [
        "right-column",
        "left-column",
        "chart-new",
        "chart-existing",
    ]
    assert items[0]["reading_order"] == 7
    assert items[1]["reading_order"] == 9
    assert items[3]["reading_order"] == 11
    assert "reading_order" not in items[2]
    assert result["document"].count("<table>") == 2


def test_chart_repair_matches_canonicalized_pipe_table_anchor() -> None:
    raw = "| Label | Value |\n| --- | --- |\n| A | narrative |"
    canonical = (
        "<table><thead><tr><th>Label</th><th>Value</th></tr></thead>"
        "<tbody><tr><td>A</td><td>narrative</td></tr></tbody></table>"
    )
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "md": canonical,
                "items": [
                    {
                        "id": "chart-1",
                        "type": "chart",
                        "md": raw,
                        "html": "",
                        "value": raw,
                    }
                ],
            }
        ],
        "pages": [{"page_index": 0, "markdown": canonical}],
        "markdown": canonical,
    }
    replacement = StructuredTable(
        ("Label", "Value"),
        (("A", "42"),),
        caption="Visible chart",
    )

    document, repaired = apply_structured_repair(
        document=canonical,
        layout=layout,
        issue={
            "reason": "filex_chart_content_unusable",
            "page_number": 1,
            "item_index": 0,
            "block_id": "chart-1",
        },
        table=replacement,
    )

    assert document == replacement.to_html()
    assert repaired["layout_pages"][0]["md"] == document
    assert repaired["pages"][0]["markdown"] == document
    assert repaired["layout_pages"][0]["items"][0]["value"] == document


def test_repair_deduplicates_caption_and_note_already_in_adjacent_items() -> None:
    document = "Annual results\n\nChart prose\n\nSource: audited filing"
    items = [
        {
            "id": "caption",
            "type": "caption",
            "md": "Annual results",
            "html": "",
            "value": "Annual results",
        },
        {
            "id": "chart-1",
            "type": "chart",
            "md": "Chart prose",
            "html": "",
            "value": "Chart prose",
        },
        {
            "id": "source-note",
            "type": "footnote",
            "md": "Source: audited filing",
            "html": "",
            "value": "Source: audited filing",
        },
    ]
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "md": document,
                "text": document,
                "items": items,
            }
        ],
        "pages": [{"page_index": 0, "markdown": document}],
        "markdown": document,
    }
    replacement = StructuredTable(
        ("Year", "Value"),
        (("2024", "42"),),
        caption="Annual results",
        notes=("Source: audited filing",),
    )

    repaired_document, repaired = apply_structured_repair(
        document=document,
        layout=layout,
        issue={
            "reason": "filex_chart_content_unusable",
            "page_number": 1,
            "item_index": 1,
            "block_id": "chart-1",
        },
        table=replacement,
    )

    chart_html = repaired["layout_pages"][0]["items"][1]["value"]
    assert "<caption>" not in chart_html
    assert "filex-structured-notes" not in chart_html
    assert repaired_document.count("Annual results") == 1
    assert repaired_document.count("Source: audited filing") == 1


def test_existing_empty_chart_item_inserts_at_neighbor_anchor() -> None:
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "md": "Introduction",
                "items": [
                    {
                        "id": "intro",
                        "type": "text",
                        "md": "Introduction",
                        "html": "",
                        "value": "Introduction",
                        "reading_order": 3,
                    },
                    {
                        "id": "chart-empty",
                        "type": "chart",
                        "md": "",
                        "html": "",
                        "value": "",
                        "bbox": {"x": 5, "y": 50, "w": 90, "h": 40, "label": "picture"},
                        "reading_order": 4,
                    },
                ],
            }
        ],
        "pages": [{"page_index": 0, "markdown": "Introduction"}],
        "markdown": "Introduction",
    }
    replacement = StructuredTable(
        ("Year", "Value"),
        (("2024", "42"),),
        caption="Printed chart",
    )

    document, repaired = apply_structured_repair(
        document="Introduction",
        layout=layout,
        issue={
            "reason": "filex_chart_content_unusable",
            "page_number": 1,
            "item_index": 1,
            "block_id": "chart-empty",
        },
        table=replacement,
    )

    assert document == f"Introduction\n\n{replacement.to_html()}"
    assert repaired["layout_pages"][0]["md"] == document
    assert repaired["pages"][0]["markdown"] == document
    assert repaired["layout_pages"][0]["items"][0]["reading_order"] == 3
    assert repaired["layout_pages"][0]["items"][1]["reading_order"] == 4
    assert repaired["layout_pages"][0]["items"][1]["value"] == replacement.to_html()


def test_chart_only_empty_page_uses_bounded_whole_scope_assignment() -> None:
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "md": "",
                "text": "",
                "items": [
                    {
                        "id": "chart-only",
                        "type": "chart",
                        "md": "",
                        "html": "",
                        "value": "",
                        "bbox": {"x": 5, "y": 5, "w": 90, "h": 90, "label": "picture"},
                        "reading_order": 0,
                    }
                ],
            }
        ],
        "pages": [{"page_index": 0, "markdown": ""}],
        "markdown": "",
    }
    replacement = StructuredTable(
        ("Year", "Value"),
        (("2024", "42"),),
        caption="Only chart",
    )

    document, repaired = apply_structured_repair(
        document="",
        layout=layout,
        issue={
            "reason": "filex_chart_content_unusable",
            "page_number": 1,
            "item_index": 0,
            "block_id": "chart-only",
        },
        table=replacement,
    )

    assert document == replacement.to_html()
    assert repaired["markdown"] == document
    assert repaired["pages"][0]["markdown"] == document
    assert repaired["layout_pages"][0]["md"] == document
    assert repaired["layout_pages"][0]["text"] == document
    assert repaired["layout_pages"][0]["items"][0]["value"] == document


def test_empty_chart_page_in_multi_page_document_uses_neighbor_page_anchor() -> None:
    layout = {
        "layout_pages": [
            {
                "page_number": 1,
                "md": "Introduction",
                "text": "Introduction",
                "items": [
                    {
                        "id": "intro",
                        "type": "text",
                        "md": "Introduction",
                        "html": "",
                        "value": "Introduction",
                    }
                ],
            },
            {
                "page_number": 2,
                "md": "",
                "text": "",
                "items": [
                    {
                        "id": "chart-only",
                        "type": "chart",
                        "md": "",
                        "html": "",
                        "value": "",
                        "bbox": {"x": 5, "y": 5, "w": 90, "h": 90, "label": "picture"},
                    }
                ],
            },
            {
                "page_number": 3,
                "md": "Conclusion",
                "text": "Conclusion",
                "items": [
                    {
                        "id": "conclusion",
                        "type": "text",
                        "md": "Conclusion",
                        "html": "",
                        "value": "Conclusion",
                    }
                ],
            },
        ],
        "pages": [
            {"page_index": 0, "markdown": "Introduction"},
            {"page_index": 1, "markdown": ""},
            {"page_index": 2, "markdown": "Conclusion"},
        ],
        "markdown": "Introduction\n\nConclusion",
    }
    replacement = StructuredTable(("Year", "Value"), (("2024", "42"),))

    document, repaired = apply_structured_repair(
        document=layout["markdown"],
        layout=layout,
        issue={
            "reason": "filex_chart_content_unusable",
            "page_number": 2,
            "item_index": 0,
            "block_id": "chart-only",
        },
        table=replacement,
    )

    assert document == f"Introduction\n\n{replacement.to_html()}\n\nConclusion"
    assert repaired["pages"][1]["markdown"] == replacement.to_html()
    assert repaired["layout_pages"][1]["md"] == replacement.to_html()
    assert repaired["layout_pages"][1]["text"] == replacement.to_html()
    assert repaired["layout_pages"][0]["md"] == "Introduction"
    assert repaired["layout_pages"][2]["md"] == "Conclusion"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_payload", "expect_repaired"),
    [
        (
            '{"caption":"Estimated chart","labels":["Country","Gender",'
            '"Value","Germany","Women"],"estimated":true,'
            '"value_columns":[2],"columns":["Country","Gender","Value"],'
            '"rows":[["Germany","Women","~42"]]}',
            True,
        ),
        (
            '{"caption":"Estimated chart","labels":["Country","Gender",'
            '"Value","Germany","Women"],"estimated":false,'
            '"value_columns":[2],"columns":["Country","Gender","Value"],'
            '"rows":[["Germany","Women","42"]]}',
            False,
        ),
    ],
)
async def test_axis_bounded_estimated_chart_value_is_applied_with_marker(
    tmp_path: Path,
    second_payload: str,
    expect_repaired: bool,
) -> None:
    @dataclass
    class _Response:
        text: str

    class _Backend:
        def __init__(self) -> None:
            self.calls = 0

        async def transcribe(self, *_args, **_kwargs):
            self.calls += 1
            return _Response(
                '{"caption":"Estimated chart","labels":["Country","Gender",'
                '"Value","Germany","Women"],"estimated":true,'
                '"value_columns":[2],"columns":["Country","Gender","Value"],'
                '"rows":[["Germany","Women","42"]]}'
                if self.calls == 1
                else second_payload
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
                "md": "Chart prose",
                "text": "Chart prose",
                "items": [
                    {
                        "id": "chart-1",
                        "type": "chart",
                        "md": "Chart prose",
                        "html": "",
                        "value": "Chart prose",
                        "bbox": {
                            "x": 10,
                            "y": 20,
                            "w": 70,
                            "h": 40,
                            "label": "picture",
                        },
                    }
                ],
            }
        ],
        "markdown": "Chart prose",
    }

    backend = _Backend()
    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document="Chart prose",
        layout=layout,
        quality_report={
            "tables": {"issues": []},
            "charts": {
                "issues": [
                    {
                        "reason": "filex_chart_content_unusable",
                        "page_number": 1,
                        "item_index": 0,
                        "block_id": "chart-1",
                        "bbox": {"x": 10, "y": 20, "w": 70, "h": 40},
                    }
                ]
            },
        },
        backend=backend,
        crop_renderer=render,
    )

    if not expect_repaired:
        assert result["repaired"] == []
        assert result["failures"][0]["reason"] == (
            "filex_chart_repair_estimated_value_unverified"
        )
        assert result["document"] == "Chart prose"
        assert backend.calls == 2
        return

    assert result["failures"] == []
    assert result["repaired"] == [
        {
            "kind": "charts",
            "page_number": 1,
            "item_index": 0,
            "block_id": "chart-1",
        }
    ]
    assert "<th>Country</th><th>Gender</th><th>Value</th>" in result["document"]
    assert "<td>Germany</td><td>Women</td><td>~42</td>" in result["document"]
    repaired_item = result["layout"]["layout_pages"][0]["items"][0]
    assert repaired_item["html"] in result["document"]
    assert repaired_item["filex_chart_value_columns"] == [2]
    assert backend.calls == 2


@pytest.mark.asyncio
async def test_document_coverage_issue_without_block_identity_fails_closed(
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document_section", "attempted"),
    [
        ({"issues": [42]}, 1),
        ({"issues": [], "issues_truncated": True}, 0),
    ],
)
async def test_malformed_or_truncated_document_issues_fail_closed(
    tmp_path: Path, document_section: dict, attempted: int
) -> None:
    layout = {"layout_pages": [], "markdown": "Document prose"}

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document="Document prose",
        layout=layout,
        quality_report={
            "tables": {"issues": []},
            "charts": {"issues": []},
            "document": document_section,
        },
        backend=object(),
    )

    assert result["document"] == "Document prose"
    assert result["layout"] == layout
    assert result["repaired"] == []
    assert result["attempted"] == attempted
    assert result["failures"] == [
        {
            "kind": "document",
            "page_number": None,
            "item_index": None,
            "block_id": None,
            "reason": "filex_block_repair_document_anchor_required",
        }
    ]


@pytest.mark.asyncio
async def test_document_coverage_repair_inserts_missing_root_table_without_vlm(
    tmp_path: Path,
) -> None:
    table = (
        "<table><thead><tr><th>Name</th><th>Value</th></tr></thead>"
        "<tbody><tr><td>A</td><td>1</td></tr></tbody></table>"
    )
    page_markdown = f"Introduction\n\n{table}"
    items = [
        {
            "id": "intro",
            "type": "text",
            "md": "Introduction",
            "html": "",
            "value": "Introduction",
            "reading_order": 0,
        },
        {
            "id": "table-1",
            "type": "table",
            "md": table,
            "html": table,
            "value": table,
            "reading_order": 1,
        },
    ]
    layout = {
        "pages": [{"page_index": 0, "markdown": page_markdown}],
        "layout_pages": [
            {
                "page_number": 1,
                "md": page_markdown,
                "text": page_markdown,
                "items": items,
            }
        ],
        "markdown": "Introduction",
    }

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document="Introduction",
        layout=layout,
        quality_report={
            "tables": {"issues": []},
            "charts": {"issues": []},
            "document": {
                "issues": [
                    {
                        "reason": "filex_document_table_coverage_incomplete",
                        "page_number": 1,
                        "item_index": 0,
                        "block_id": "table-1",
                    }
                ]
            },
        },
        backend=object(),
    )

    assert result["document"] == page_markdown
    assert result["layout"]["layout_pages"][0]["md"] == page_markdown
    assert result["layout"]["pages"][0]["markdown"] == page_markdown
    assert [item["id"] for item in result["layout"]["layout_pages"][0]["items"]] == [
        "intro",
        "table-1",
    ]
    assert result["repaired"] == [
        {
            "kind": "document",
            "page_number": 1,
            "item_index": 1,
            "block_id": "table-1",
        }
    ]
    assert result["failures"] == []


@pytest.mark.asyncio
async def test_document_coverage_repair_synchronizes_missing_page_views(
    tmp_path: Path,
) -> None:
    table = "<table><tr><th>A</th><th>B</th></tr><tr><td>x</td><td>1</td></tr></table>"
    layout = {
        "pages": [{"page_index": 0, "markdown": "Anchor"}],
        "layout_pages": [
            {
                "page_number": 1,
                "md": "Anchor",
                "text": f"Anchor\n\n{table}",
                "items": [
                    {
                        "id": "anchor",
                        "type": "text",
                        "md": "Anchor",
                        "html": "",
                        "value": "Anchor",
                    },
                    {
                        "id": "table-1",
                        "type": "table",
                        "md": table,
                        "html": table,
                        "value": table,
                    },
                ],
            }
        ],
        "markdown": "Anchor",
    }
    issue = {
        "reason": "filex_document_table_coverage_incomplete",
        "page_number": 1,
        "item_index": 1,
        "block_id": "table-1",
    }

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document="Anchor",
        layout=layout,
        quality_report={
            "tables": {"issues": []},
            "charts": {"issues": []},
            "document": {"issues": [issue]},
        },
        backend=object(),
    )

    expected = f"Anchor\n\n{table}"
    assert result["document"] == expected
    assert result["layout"]["markdown"] == expected
    assert result["layout"]["layout_pages"][0]["md"] == expected
    assert result["layout"]["pages"][0]["markdown"] == expected
    assert result["layout"]["layout_pages"][0]["text"] == expected


@pytest.mark.asyncio
async def test_document_coverage_repair_preserves_duplicate_table_multiplicity(
    tmp_path: Path,
) -> None:
    table = "<table><tr><th>A</th><th>B</th></tr><tr><td>x</td><td>1</td></tr></table>"
    complete_page = f"{table}\n\nBetween\n\n{table}\n\nTail"
    items = [
        {"id": "table-a", "type": "table", "md": table, "html": table, "value": table},
        {
            "id": "between",
            "type": "text",
            "md": "Between",
            "html": "",
            "value": "Between",
        },
        {"id": "table-b", "type": "table", "md": table, "html": table, "value": table},
        {"id": "tail", "type": "text", "md": "Tail", "html": "", "value": "Tail"},
    ]
    layout = {
        "pages": [{"page_index": 0, "markdown": complete_page}],
        "layout_pages": [
            {
                "page_number": 1,
                "md": complete_page,
                "text": complete_page,
                "items": items,
            }
        ],
        "markdown": f"{table}\n\nBetween\n\nTail",
    }

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document=layout["markdown"],
        layout=layout,
        quality_report={
            "tables": {"issues": []},
            "charts": {"issues": []},
            "document": {
                "issues": [
                    {
                        "reason": "filex_document_table_coverage_incomplete",
                        "page_number": 1,
                        "item_index": 0,
                        "block_id": "table-b",
                    }
                ]
            },
        },
        backend=object(),
    )

    assert result["document"] == complete_page
    assert result["document"].count(table) == 2
    assert result["layout"]["layout_pages"][0]["md"] == complete_page
    assert [item["id"] for item in result["layout"]["layout_pages"][0]["items"]] == [
        "table-a",
        "between",
        "table-b",
        "tail",
    ]


@pytest.mark.asyncio
async def test_multiple_document_coverage_repairs_relocate_each_block_by_id(
    tmp_path: Path,
) -> None:
    table_a = (
        "<table><tr><th>A</th><th>V</th></tr><tr><td>a</td><td>1</td></tr></table>"
    )
    table_b = (
        "<table><tr><th>B</th><th>V</th></tr><tr><td>b</td><td>2</td></tr></table>"
    )
    complete_page = f"Intro\n\n{table_a}\n\nMiddle\n\n{table_b}\n\nEnd"
    items = [
        {"id": "intro", "type": "text", "md": "Intro", "html": "", "value": "Intro"},
        {
            "id": "table-a",
            "type": "table",
            "md": table_a,
            "html": table_a,
            "value": table_a,
        },
        {"id": "middle", "type": "text", "md": "Middle", "html": "", "value": "Middle"},
        {
            "id": "table-b",
            "type": "table",
            "md": table_b,
            "html": table_b,
            "value": table_b,
        },
        {"id": "end", "type": "text", "md": "End", "html": "", "value": "End"},
    ]
    layout = {
        "pages": [{"page_index": 0, "markdown": complete_page}],
        "layout_pages": [
            {
                "page_number": 1,
                "md": complete_page,
                "text": complete_page,
                "items": items,
            }
        ],
        "markdown": "Intro\n\nMiddle\n\nEnd",
    }
    issues = [
        {
            "reason": "filex_document_table_coverage_incomplete",
            "page_number": 1,
            "item_index": 99,
            "block_id": "table-a",
        },
        {
            "reason": "filex_document_table_coverage_incomplete",
            "page_number": 1,
            "item_index": 0,
            "block_id": "table-b",
        },
    ]

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document=layout["markdown"],
        layout=layout,
        quality_report={
            "tables": {"issues": []},
            "charts": {"issues": []},
            "document": {"issues": issues},
        },
        backend=object(),
    )

    assert result["document"] == complete_page
    assert [repair["item_index"] for repair in result["repaired"]] == [1, 3]
    assert result["failures"] == []
    assert result["attempted"] == 2
    assert result["remaining"] == 0


@pytest.mark.asyncio
async def test_document_coverage_repair_fails_on_ambiguous_neighbor_anchor(
    tmp_path: Path,
) -> None:
    table = "<table><tr><th>A</th><th>B</th></tr><tr><td>x</td><td>1</td></tr></table>"
    page_markdown = f"{table}\n\nAnchor"
    layout = {
        "pages": [{"page_index": 0, "markdown": page_markdown}],
        "layout_pages": [
            {
                "page_number": 1,
                "md": page_markdown,
                "text": page_markdown,
                "items": [
                    {
                        "id": "table-1",
                        "type": "table",
                        "md": table,
                        "html": table,
                        "value": table,
                    },
                    {
                        "id": "anchor",
                        "type": "text",
                        "md": "Anchor",
                        "html": "",
                        "value": "Anchor",
                    },
                ],
            }
        ],
        "markdown": "Anchor\n\nAnchor",
    }

    result = await repair_parse_output(
        source_path=tmp_path / "source.pdf",
        document=layout["markdown"],
        layout=layout,
        quality_report={
            "tables": {"issues": []},
            "charts": {"issues": []},
            "document": {
                "issues": [
                    {
                        "reason": "filex_document_table_coverage_incomplete",
                        "page_number": 1,
                        "item_index": 0,
                        "block_id": "table-1",
                    }
                ]
            },
        },
        backend=object(),
    )

    assert result["document"] == layout["markdown"]
    assert result["layout"] == layout
    assert result["repaired"] == []
    assert result["failures"][0]["reason"] == (
        "filex_block_repair_document_anchor_ambiguous"
    )
