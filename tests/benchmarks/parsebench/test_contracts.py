from __future__ import annotations

from aworld.benchmarks.parsebench.contracts import (
    DATASET_REVISION,
    EXPECTED_SOURCE_FIELDS,
    PARSEBENCH_TASK_FILENAME,
    PARSEBENCH_TASK_RUNTIME_PATH,
    PARSEBENCH_TASK_SCHEMA_VERSION,
    PINNED_PARSEBENCH_CONTRACT,
    SCORER_REVISION,
    ParseBenchDimension,
)


def test_pinned_parsebench_contract_models_all_upstream_dimensions() -> None:
    assert DATASET_REVISION == "2805a1d940f95a203e0ae4b88be9934f7765b3fc"
    assert SCORER_REVISION == "34b73455032797754f6ed62e14c27a8b5423d11e"
    assert PARSEBENCH_TASK_SCHEMA_VERSION == "aworld-parsebench-task/v1"
    assert PARSEBENCH_TASK_FILENAME == "parsebench-task.json"
    assert PARSEBENCH_TASK_RUNTIME_PATH == "/workspace/parsebench-task.json"
    assert EXPECTED_SOURCE_FIELDS == frozenset(
        {
            "pdf",
            "category",
            "id",
            "type",
            "rule",
            "page",
            "expected_markdown",
            "tags",
        }
    )
    assert PINNED_PARSEBENCH_CONTRACT.source_files == (
        (ParseBenchDimension.CHART, "chart.jsonl"),
        (ParseBenchDimension.LAYOUT, "layout.jsonl"),
        (ParseBenchDimension.TABLE, "table.jsonl"),
        (ParseBenchDimension.TEXT_CONTENT, "text_content.jsonl"),
        (ParseBenchDimension.TEXT_FORMATTING, "text_formatting.jsonl"),
    )
    assert PINNED_PARSEBENCH_CONTRACT.rule_counts == (
        (ParseBenchDimension.CHART, 4_864),
        (ParseBenchDimension.LAYOUT, 16_325),
        (ParseBenchDimension.TABLE, 503),
        (ParseBenchDimension.TEXT_CONTENT, 141_322),
        (ParseBenchDimension.TEXT_FORMATTING, 5_997),
    )
    assert PINNED_PARSEBENCH_CONTRACT.dimension_execution_counts == (
        (ParseBenchDimension.CHART, 568),
        (ParseBenchDimension.LAYOUT, 500),
        (ParseBenchDimension.TABLE, 503),
        (ParseBenchDimension.TEXT_CONTENT, 506),
        (ParseBenchDimension.TEXT_FORMATTING, 476),
    )
    assert PINNED_PARSEBENCH_CONTRACT.total_rule_count == 169_011
    assert PINNED_PARSEBENCH_CONTRACT.unique_execution_count == 2_078
