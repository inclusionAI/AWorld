from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aworld.benchmarks.parsebench.scoring import (
    PARSEBENCH_DATA_REVISION,
    PARSEBENCH_DATASET_ID,
    PARSEBENCH_CASE_RESULT_SCHEMA,
    PARSEBENCH_DIMENSIONS,
    PARSEBENCH_PRIMARY_METRICS,
    PARSEBENCH_REPORT_SCHEMA,
    PARSEBENCH_SCORER_REPOSITORY,
    PARSEBENCH_SCORER_REVISION,
    OfficialScorerEnvironment,
    OfficialScorerValidationError,
    ParseBenchCaseResult,
    ParseBenchDimension,
    ParseBenchDimensionResult,
    ParseBenchOverallStatus,
    ParseBenchResultStatus,
    dimension_result_from_official_report,
    reduce_parsebench_case_results,
    reduce_parsebench_results,
    run_official_scorer,
    validate_official_scorer,
)


def _scored(
    dimension: ParseBenchDimension,
    score: float,
    *,
    total_examples: int = 1,
) -> ParseBenchDimensionResult:
    return ParseBenchDimensionResult.scored(
        dimension=dimension,
        score=score,
        total_examples=total_examples,
        successful_examples=total_examples,
        aggregate_metrics={PARSEBENCH_PRIMARY_METRICS[dimension]: score},
    )


def test_upstream_revisions_and_primary_metrics_are_pinned() -> None:
    assert PARSEBENCH_DATASET_ID == "llamaindex/ParseBench"
    assert PARSEBENCH_DATA_REVISION == "2805a1d940f95a203e0ae4b88be9934f7765b3fc"
    assert PARSEBENCH_SCORER_REPOSITORY == "https://github.com/run-llama/ParseBench.git"
    assert PARSEBENCH_SCORER_REVISION == "34b73455032797754f6ed62e14c27a8b5423d11e"
    assert PARSEBENCH_DIMENSIONS == (
        ParseBenchDimension.TABLE,
        ParseBenchDimension.CHART,
        ParseBenchDimension.TEXT_CONTENT,
        ParseBenchDimension.TEXT_FORMATTING,
        ParseBenchDimension.LAYOUT,
    )
    assert PARSEBENCH_PRIMARY_METRICS == {
        ParseBenchDimension.TABLE: "avg_grits_trm_composite",
        ParseBenchDimension.CHART: "avg_rule_pass_rate",
        ParseBenchDimension.TEXT_CONTENT: "avg_content_faithfulness",
        ParseBenchDimension.TEXT_FORMATTING: "avg_semantic_formatting",
        ParseBenchDimension.LAYOUT: "avg_layout_element_rule_pass_rate",
    }


def test_official_report_preserves_a_real_zero_score() -> None:
    result = dimension_result_from_official_report(
        ParseBenchDimension.CHART,
        {
            "total_examples": 2,
            "successful": 2,
            "failed": 0,
            "skipped": 0,
            "aggregate_metrics": {
                "avg_rule_pass_rate": 0.0,
                "total_rule_pass_rate_evaluated": 8.0,
            },
            "per_example_results": [],
        },
    )

    assert result.status is ParseBenchResultStatus.SCORED
    assert result.score == 0.0
    assert result.primary_metric == "avg_rule_pass_rate"
    assert result.aggregate_metrics["total_rule_pass_rate_evaluated"] == 8.0


def test_official_report_keeps_not_scored_distinct_from_zero() -> None:
    result = dimension_result_from_official_report(
        ParseBenchDimension.LAYOUT,
        {
            "total_examples": 0,
            "successful": 0,
            "failed": 0,
            "skipped": 3,
            "aggregate_metrics": {},
            "per_example_results": [],
        },
    )

    assert result.status is ParseBenchResultStatus.NOT_SCORED
    assert result.score is None
    assert result.skipped_examples == 3


def test_official_report_keeps_execution_failure_distinct_from_zero() -> None:
    result = dimension_result_from_official_report(
        ParseBenchDimension.TABLE,
        {
            "total_examples": 1,
            "successful": 0,
            "failed": 1,
            "skipped": 0,
            "aggregate_metrics": {},
            "per_example_results": [
                {
                    "test_id": "table/example",
                    "success": False,
                    "error": "provider produced no usable output",
                }
            ],
        },
    )

    assert result.status is ParseBenchResultStatus.EXECUTION_FAILED
    assert result.score is None
    assert result.errors == ("table/example: provider produced no usable output",)


def test_official_report_with_no_applicable_primary_metric_is_not_scored() -> None:
    result = dimension_result_from_official_report(
        ParseBenchDimension.TEXT_FORMATTING,
        {
            "total_examples": 1,
            "successful": 1,
            "failed": 0,
            "skipped": 0,
            "aggregate_metrics": {"avg_rule_pass_rate": 1.0},
            "per_example_results": [
                {
                    "test_id": "formatting/no-applicable-category",
                    "success": True,
                    "metrics": [{"metric_name": "rule_pass_rate", "value": 1.0}],
                }
            ],
        },
    )

    assert result.status is ParseBenchResultStatus.NOT_SCORED
    assert result.score is None
    assert result.numeric_examples == 0
    assert result.not_scored_examples == 1


def test_official_report_counts_numeric_and_not_scored_cases_separately() -> None:
    result = dimension_result_from_official_report(
        ParseBenchDimension.TEXT_FORMATTING,
        {
            "total_examples": 2,
            "successful": 2,
            "failed": 0,
            "skipped": 0,
            "aggregate_metrics": {"avg_semantic_formatting": 0.25},
            "per_example_results": [
                {
                    "test_id": "formatting/scored",
                    "success": True,
                    "metrics": [{"metric_name": "semantic_formatting", "value": 0.25}],
                },
                {
                    "test_id": "formatting/not-scored",
                    "success": True,
                    "metrics": [{"metric_name": "rule_pass_rate", "value": 1.0}],
                },
            ],
        },
    )

    assert result.status is ParseBenchResultStatus.SCORED
    assert result.score == pytest.approx(0.25)
    assert result.numeric_examples == 1
    assert result.not_scored_examples == 1


@pytest.mark.parametrize("bad_score", [-0.01, 1.01, float("nan"), float("inf"), True])
def test_dimension_score_rejects_non_probability_values(bad_score: float) -> None:
    with pytest.raises((TypeError, ValueError)):
        _scored(ParseBenchDimension.CHART, bad_score)


def test_reducer_matches_published_equal_weight_golden_score() -> None:
    # LlamaParse Cost Effective values at the pinned scorer revision. Unequal
    # example counts make this a regression guard against task-weighted means.
    results = [
        _scored(ParseBenchDimension.TABLE, 0.8142, total_examples=503),
        _scored(ParseBenchDimension.CHART, 0.7015, total_examples=568),
        _scored(ParseBenchDimension.TEXT_CONTENT, 0.9092, total_examples=506),
        _scored(ParseBenchDimension.TEXT_FORMATTING, 0.6878, total_examples=476),
        _scored(ParseBenchDimension.LAYOUT, 0.7259, total_examples=500),
    ]

    report = reduce_parsebench_results(results)

    assert report.status is ParseBenchOverallStatus.SCORED
    assert report.overall_score == pytest.approx(0.76772)
    assert report.overall_percent == pytest.approx(76.772)
    assert report.missing_dimensions == ()
    assert report.failed_dimensions == ()
    assert report.not_scored_dimensions == ()


def test_reducer_treats_zero_as_a_scored_dimension() -> None:
    results = [_scored(dimension, 0.5) for dimension in PARSEBENCH_DIMENSIONS]
    results[1] = _scored(ParseBenchDimension.CHART, 0.0)

    report = reduce_parsebench_results(results)

    assert report.status is ParseBenchOverallStatus.SCORED
    assert report.overall_score == pytest.approx(0.4)


def test_reducer_reports_missing_dimensions_without_partial_overall() -> None:
    report = reduce_parsebench_results(
        [_scored(dimension, 1.0) for dimension in PARSEBENCH_DIMENSIONS[:-1]]
    )

    assert report.status is ParseBenchOverallStatus.MISSING_DIMENSIONS
    assert report.overall_score is None
    assert report.missing_dimensions == (ParseBenchDimension.LAYOUT,)


def test_reducer_reports_not_scored_without_converting_it_to_zero() -> None:
    results = [_scored(dimension, 1.0) for dimension in PARSEBENCH_DIMENSIONS]
    results[2] = ParseBenchDimensionResult.not_scored(
        dimension=ParseBenchDimension.TEXT_CONTENT,
        skipped_examples=1,
    )

    report = reduce_parsebench_results(results)

    assert report.status is ParseBenchOverallStatus.NOT_SCORED
    assert report.overall_score is None
    assert report.not_scored_dimensions == (ParseBenchDimension.TEXT_CONTENT,)


def test_reducer_reports_execution_failure_without_converting_it_to_zero() -> None:
    results = [_scored(dimension, 1.0) for dimension in PARSEBENCH_DIMENSIONS]
    results[4] = ParseBenchDimensionResult.execution_failed(
        dimension=ParseBenchDimension.LAYOUT,
        errors=("official scorer timed out",),
    )

    report = reduce_parsebench_results(results)

    assert report.status is ParseBenchOverallStatus.EXECUTION_FAILED
    assert report.overall_score is None
    assert report.failed_dimensions == (ParseBenchDimension.LAYOUT,)


def test_reducer_rejects_duplicate_dimensions() -> None:
    with pytest.raises(ValueError, match="duplicate.*chart"):
        reduce_parsebench_results(
            [
                _scored(ParseBenchDimension.CHART, 0.1),
                _scored(ParseBenchDimension.CHART, 0.2),
            ]
        )


def test_case_reducer_averages_only_numeric_scores_and_preserves_zero() -> None:
    report = reduce_parsebench_case_results(
        [
            ParseBenchCaseResult.scored(
                case_id="table/zero",
                dimension=ParseBenchDimension.TABLE,
                score=0.0,
            ),
            ParseBenchCaseResult.scored(
                case_id="table/one",
                dimension=ParseBenchDimension.TABLE,
                score=1.0,
            ),
            ParseBenchCaseResult.not_scored(
                case_id="table/not-applicable",
                dimension=ParseBenchDimension.TABLE,
                diagnostics=("no applicable official metric",),
            ),
        ]
    )

    dimension = report.dimensions[0]
    assert report.publishable is False  # the other four dimensions are absent
    assert report.status is ParseBenchOverallStatus.MISSING_DIMENSIONS
    assert dimension.status is ParseBenchResultStatus.SCORED
    assert dimension.score == pytest.approx(0.5)
    assert dimension.counts == {
        "numeric": 2,
        "not_scored": 1,
        "failed": 0,
        "missing": 0,
    }
    assert report.counts == dimension.counts


def test_case_reducer_can_publish_with_not_scored_cases_excluded() -> None:
    cases = [
        ParseBenchCaseResult.scored(
            case_id=f"{dimension.value}/numeric",
            dimension=dimension,
            score=(index + 1) / 10,
        )
        for index, dimension in enumerate(PARSEBENCH_DIMENSIONS)
    ]
    cases.append(
        ParseBenchCaseResult.not_scored(
            case_id="text_formatting/not-applicable",
            dimension=ParseBenchDimension.TEXT_FORMATTING,
        )
    )

    report = reduce_parsebench_case_results(cases)

    assert report.publishable is True
    assert report.status is ParseBenchOverallStatus.SCORED
    assert report.overall_score == pytest.approx(0.3)
    assert report.counts == {
        "numeric": 5,
        "not_scored": 1,
        "failed": 0,
        "missing": 0,
    }
    formatting = next(
        result
        for result in report.dimensions
        if result.dimension is ParseBenchDimension.TEXT_FORMATTING
    )
    assert formatting.score == pytest.approx(0.4)
    assert formatting.not_scored_examples == 1


def test_case_reducer_matches_filex_pinned_baseline_golden() -> None:
    scores = {
        ParseBenchDimension.TABLE: 0.676422,
        ParseBenchDimension.CHART: 0.561396,
        ParseBenchDimension.TEXT_CONTENT: 0.828802,
        ParseBenchDimension.TEXT_FORMATTING: 0.484013,
        ParseBenchDimension.LAYOUT: 0.052995,
    }
    cases = [
        ParseBenchCaseResult.scored(
            case_id=f"{dimension.value}/numeric",
            dimension=dimension,
            score=score,
        )
        for dimension, score in scores.items()
    ]
    cases.extend(
        ParseBenchCaseResult.not_scored(
            case_id=f"text_formatting/not-scored-{index:02d}",
            dimension=ParseBenchDimension.TEXT_FORMATTING,
        )
        for index in range(19)
    )

    report = reduce_parsebench_case_results(cases)

    assert report.publishable is True
    assert report.overall_score == pytest.approx(0.5207256)
    assert report.overall_percent == pytest.approx(52.07256)
    assert report.counts == {
        "numeric": 5,
        "not_scored": 19,
        "failed": 0,
        "missing": 0,
    }


def test_case_reducer_failure_blocks_publish_but_keeps_diagnostic_mean() -> None:
    cases = [
        ParseBenchCaseResult.scored(
            case_id=f"{dimension.value}/numeric",
            dimension=dimension,
            score=1.0,
        )
        for dimension in PARSEBENCH_DIMENSIONS
    ]
    cases.append(
        ParseBenchCaseResult.execution_failed(
            case_id="chart/failed",
            dimension=ParseBenchDimension.CHART,
            diagnostics=("worker exited 137",),
        )
    )

    report = reduce_parsebench_case_results(cases)

    assert report.publishable is False
    assert report.status is ParseBenchOverallStatus.EXECUTION_FAILED
    assert report.overall_score is None
    chart = next(
        result
        for result in report.dimensions
        if result.dimension is ParseBenchDimension.CHART
    )
    assert chart.score is None
    assert chart.diagnostic_score == pytest.approx(1.0)
    assert chart.failed_examples == 1
    assert "chart: 1 execution failure(s)" in report.diagnostics


def test_case_reducer_expected_manifest_detects_missing_results() -> None:
    cases = [
        ParseBenchCaseResult.scored(
            case_id=f"{dimension.value}/present",
            dimension=dimension,
            score=0.5,
        )
        for dimension in PARSEBENCH_DIMENSIONS
    ]
    expected = {
        dimension: [f"{dimension.value}/present"] for dimension in PARSEBENCH_DIMENSIONS
    }
    expected[ParseBenchDimension.LAYOUT].append("layout/missing")

    report = reduce_parsebench_case_results(cases, expected_case_ids=expected)

    assert report.publishable is False
    assert report.status is ParseBenchOverallStatus.MISSING_RESULTS
    assert report.overall_score is None
    assert report.counts == {
        "numeric": 5,
        "not_scored": 0,
        "failed": 0,
        "missing": 1,
    }
    layout = next(
        result
        for result in report.dimensions
        if result.dimension is ParseBenchDimension.LAYOUT
    )
    assert layout.status is ParseBenchResultStatus.MISSING
    assert layout.score is None
    assert layout.diagnostic_score == pytest.approx(0.5)
    assert layout.missing_case_ids == ("layout/missing",)
    assert "layout: 1 missing result(s)" in report.diagnostics


def test_case_reducer_all_not_scored_dimension_is_not_publishable() -> None:
    cases = [
        ParseBenchCaseResult.scored(
            case_id=f"{dimension.value}/numeric",
            dimension=dimension,
            score=1.0,
        )
        for dimension in PARSEBENCH_DIMENSIONS
        if dimension is not ParseBenchDimension.TEXT_FORMATTING
    ]
    cases.append(
        ParseBenchCaseResult.not_scored(
            case_id="text_formatting/not-applicable",
            dimension=ParseBenchDimension.TEXT_FORMATTING,
        )
    )

    report = reduce_parsebench_case_results(cases)

    assert report.publishable is False
    assert report.status is ParseBenchOverallStatus.NOT_SCORED
    assert report.overall_score is None
    assert report.not_scored_dimensions == (ParseBenchDimension.TEXT_FORMATTING,)


def test_case_reducer_rejects_duplicate_or_unexpected_case_results() -> None:
    case = ParseBenchCaseResult.scored(
        case_id="chart/one",
        dimension=ParseBenchDimension.CHART,
        score=0.5,
    )
    with pytest.raises(ValueError, match="duplicate.*chart/one"):
        reduce_parsebench_case_results([case, case])

    with pytest.raises(ValueError, match="not present in the expected manifest"):
        reduce_parsebench_case_results(
            [case],
            expected_case_ids={ParseBenchDimension.CHART: ["chart/two"]},
        )


def test_case_reducer_rejects_raw_task_rewards() -> None:
    with pytest.raises(TypeError, match="raw task rewards"):
        reduce_parsebench_case_results([0.5])  # type: ignore[list-item]


def test_case_result_serialization_keeps_status_and_diagnostics() -> None:
    result = ParseBenchCaseResult.missing(
        case_id="layout/missing",
        dimension=ParseBenchDimension.LAYOUT,
        diagnostics=("gateway returned no artifact",),
    )

    assert result.to_dict() == {
        "schema": PARSEBENCH_CASE_RESULT_SCHEMA,
        "case_id": "layout/missing",
        "dimension": "layout",
        "status": "missing",
        "score": None,
        "score_percent": None,
        "primary_metric": "avg_layout_element_rule_pass_rate",
        "aggregate_metrics": {},
        "diagnostics": ["gateway returned no artifact"],
    }
    assert ParseBenchCaseResult.from_dict(result.to_dict()) == result


def test_case_result_rejects_unknown_artifact_schema() -> None:
    with pytest.raises(ValueError, match="case-result/v1"):
        ParseBenchCaseResult.from_dict(
            {
                "schema": "aworld.parsebench.case-result/v999",
                "case_id": "chart/one",
                "dimension": "chart",
                "status": "scored",
                "score": 1.0,
            }
        )


def test_report_serialization_is_deterministic_and_detailed() -> None:
    report = reduce_parsebench_results(
        [
            _scored(dimension, index / 10)
            for index, dimension in enumerate(PARSEBENCH_DIMENSIONS)
        ]
    )

    payload = report.to_dict()

    assert payload == {
        "schema": PARSEBENCH_REPORT_SCHEMA,
        "dataset_id": PARSEBENCH_DATASET_ID,
        "data_revision": PARSEBENCH_DATA_REVISION,
        "scorer_repository": PARSEBENCH_SCORER_REPOSITORY,
        "scorer_revision": PARSEBENCH_SCORER_REVISION,
        "status": "scored",
        "publishable": True,
        "overall_score": pytest.approx(0.2),
        "overall_percent": pytest.approx(20.0),
        "counts": {
            "numeric": 5,
            "not_scored": 0,
            "failed": 0,
            "missing": 0,
        },
        "missing_dimensions": [],
        "missing_result_dimensions": [],
        "failed_dimensions": [],
        "not_scored_dimensions": [],
        "diagnostics": [],
        "dimensions": [result.to_dict() for result in report.dimensions],
    }


def test_validate_official_scorer_checks_revision_cleanliness_and_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "ParseBench"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "src" / "parse_bench").mkdir(parents=True)
    python_executable = Path(sys.executable).resolve()
    calls: list[tuple[str, ...]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        if command[:4] == ["git", "-C", str(checkout.resolve()), "rev-parse"]:
            return subprocess.CompletedProcess(
                command, 0, PARSEBENCH_SCORER_REVISION + "\n", ""
            )
        if command[:4] == ["git", "-C", str(checkout.resolve()), "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        probe = {
            "module_file": str(
                (checkout / "src" / "parse_bench" / "__init__.py").resolve()
            ),
            "python": [3, 12, 4],
            "default_metrics": {
                "table": "grits_trm_composite",
                "layout": "layout_element_rule_pass_rate",
                "text_content": "content_faithfulness",
                "text_formatting": "semantic_formatting",
            },
            "has_evaluation_cli": True,
            "has_evaluation_summary": True,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(probe) + "\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    environment = validate_official_scorer(
        checkout, python_executable=python_executable
    )

    assert environment.checkout == checkout.resolve()
    assert environment.source_root == (checkout / "src").resolve()
    assert environment.python_executable == python_executable
    assert environment.scorer_revision == PARSEBENCH_SCORER_REVISION
    assert len(calls) == 3


def test_validate_official_scorer_preserves_virtualenv_python_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "ParseBench"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "src" / "parse_bench").mkdir(parents=True)
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(sys.executable)

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "git" and "rev-parse" in command:
            return subprocess.CompletedProcess(
                command, 0, PARSEBENCH_SCORER_REVISION + "\n", ""
            )
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0, "", "")
        probe = {
            "module_file": str(checkout / "src" / "parse_bench" / "__init__.py"),
            "python": [3, 12, 4],
            "default_metrics": {
                "table": "grits_trm_composite",
                "layout": "layout_element_rule_pass_rate",
                "text_content": "content_faithfulness",
                "text_formatting": "semantic_formatting",
            },
            "has_evaluation_cli": True,
            "has_evaluation_summary": True,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(probe), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    environment = validate_official_scorer(
        checkout,
        python_executable=venv_python,
    )

    assert environment.python_executable == venv_python.absolute()


def test_validate_official_scorer_rejects_wrong_or_dirty_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "ParseBench"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "src" / "parse_bench").mkdir(parents=True)

    def wrong_revision(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "0" * 40 + "\n", "")

    monkeypatch.setattr(subprocess, "run", wrong_revision)
    with pytest.raises(OfficialScorerValidationError, match="revision"):
        validate_official_scorer(checkout)

    call_number = 0

    def dirty_checkout(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal call_number
        call_number += 1
        output = (
            PARSEBENCH_SCORER_REVISION + "\n" if call_number == 1 else " M scorer.py\n"
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(subprocess, "run", dirty_checkout)
    with pytest.raises(OfficialScorerValidationError, match="uncommitted"):
        validate_official_scorer(checkout)


def test_official_scorer_command_is_pinned_and_disables_optional_llm_normalization(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "ParseBench"
    source_root = checkout / "src"
    environment = OfficialScorerEnvironment(
        checkout=checkout,
        source_root=source_root,
        python_executable=Path(sys.executable),
    )

    command = environment.evaluation_command(
        dimension=ParseBenchDimension.CHART,
        output_dir=tmp_path / "inference",
        test_cases_dir=tmp_path / "tests",
        report_dir=tmp_path / "report",
        max_workers=2,
    )
    child_environment = environment.subprocess_environment({"PYTHONPATH": "/existing"})

    assert command == (
        str(Path(sys.executable)),
        "-m",
        "parse_bench.cli",
        "evaluation",
        "run",
        f"--output_dir={tmp_path / 'inference'}",
        f"--test_cases_dir={tmp_path / 'tests'}",
        "--group=chart",
        f"--report_dir={tmp_path / 'report'}",
        "--export_csv=False",
        "--export_rule_csv=False",
        "--export_markdown=False",
        "--export_html=False",
        "--force=True",
        "--max_workers=2",
    )
    assert child_environment["PYTHONPATH"].split(":", 1) == [
        str(source_root),
        "/existing",
    ]
    assert child_environment["LLAMACLOUD_BENCH_LLM_NORMALIZATION"] == "off"


def test_run_official_scorer_loads_generated_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "ParseBench"
    inference_dir = tmp_path / "inference"
    test_cases_dir = tmp_path / "test-cases"
    report_dir = tmp_path / "report"
    for path in (checkout, inference_dir, test_cases_dir):
        path.mkdir()
    environment = OfficialScorerEnvironment(
        checkout=checkout,
        source_root=checkout / "src",
        python_executable=Path(sys.executable),
    )

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        report_dir.mkdir()
        (report_dir / "_evaluation_report.json").write_text(
            json.dumps(
                {
                    "total_examples": 1,
                    "successful": 1,
                    "failed": 0,
                    "skipped": 0,
                    "aggregate_metrics": {"avg_semantic_formatting": 0.25},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_official_scorer(
        environment,
        dimension=ParseBenchDimension.TEXT_FORMATTING,
        output_dir=inference_dir,
        test_cases_dir=test_cases_dir,
        report_dir=report_dir,
    )

    assert result.status is ParseBenchResultStatus.SCORED
    assert result.score == 0.25


def test_run_official_scorer_returns_failure_instead_of_fabricating_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "ParseBench"
    inference_dir = tmp_path / "inference"
    test_cases_dir = tmp_path / "test-cases"
    report_dir = tmp_path / "report"
    for path in (checkout, inference_dir, test_cases_dir):
        path.mkdir()
    environment = OfficialScorerEnvironment(
        checkout=checkout,
        source_root=checkout / "src",
        python_executable=Path(sys.executable),
    )

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "scorer crashed"
        ),
    )

    result = run_official_scorer(
        environment,
        dimension=ParseBenchDimension.TABLE,
        output_dir=inference_dir,
        test_cases_dir=test_cases_dir,
        report_dir=report_dir,
    )

    assert result.status is ParseBenchResultStatus.EXECUTION_FAILED
    assert result.score is None
    assert result.errors == ("official scorer exited with code 1: scorer crashed",)
