"""Pinned adapter and result reduction for the official ParseBench scorer.

This module deliberately does not contain ParseBench metric implementations.
It binds a clean checkout of the official scorer at a known revision, invokes
that scorer in a separate Python process, and reduces its five official
dimension scores into the leaderboard overall score.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

PARSEBENCH_DATASET_ID = "llamaindex/ParseBench"
PARSEBENCH_DATA_REVISION = "2805a1d940f95a203e0ae4b88be9934f7765b3fc"
PARSEBENCH_SCORER_REPOSITORY = "https://github.com/run-llama/ParseBench.git"
PARSEBENCH_SCORER_REVISION = "34b73455032797754f6ed62e14c27a8b5423d11e"
PARSEBENCH_CASE_RESULT_SCHEMA = "aworld.parsebench.case-result/v1"
PARSEBENCH_REPORT_SCHEMA = "aworld.parsebench.report/v1"


class ParseBenchDimension(str, Enum):
    """The five official ParseBench leaderboard dimensions."""

    TABLE = "table"
    CHART = "chart"
    TEXT_CONTENT = "text_content"
    TEXT_FORMATTING = "text_formatting"
    LAYOUT = "layout"


PARSEBENCH_DIMENSIONS = (
    ParseBenchDimension.TABLE,
    ParseBenchDimension.CHART,
    ParseBenchDimension.TEXT_CONTENT,
    ParseBenchDimension.TEXT_FORMATTING,
    ParseBenchDimension.LAYOUT,
)

# These are the aggregate keys emitted by the pinned official scorer. The
# corresponding unprefixed defaults are defined by ParseBench's aggregation
# and leaderboard reports. Chart intentionally uses the official fallback.
PARSEBENCH_PRIMARY_METRICS: Mapping[ParseBenchDimension, str] = MappingProxyType(
    {
        ParseBenchDimension.TABLE: "avg_grits_trm_composite",
        ParseBenchDimension.CHART: "avg_rule_pass_rate",
        ParseBenchDimension.TEXT_CONTENT: "avg_content_faithfulness",
        ParseBenchDimension.TEXT_FORMATTING: "avg_semantic_formatting",
        ParseBenchDimension.LAYOUT: "avg_layout_element_rule_pass_rate",
    }
)

_UPSTREAM_DEFAULT_METRICS = {
    "table": "grits_trm_composite",
    "layout": "layout_element_rule_pass_rate",
    "text_content": "content_faithfulness",
    "text_formatting": "semantic_formatting",
}
_MINIMUM_SCORER_PYTHON = (3, 12)
_OFFICIAL_REPORT_FILENAME = "_evaluation_report.json"
# Importing the full official evaluation stack can cold-start NumPy/Numba on a
# freshly provisioned runtime. Keep validation bounded while allowing that
# one-time import cost.
_PROBE_TIMEOUT_SECONDS = 120.0
_ERROR_TEXT_LIMIT = 2_000


class ParseBenchResultStatus(str, Enum):
    """Outcome of official scoring for one dimension."""

    SCORED = "scored"
    NOT_SCORED = "not_scored"
    EXECUTION_FAILED = "execution_failed"
    MISSING = "missing"


class ParseBenchOverallStatus(str, Enum):
    """Whether an official five-dimension overall score is available."""

    SCORED = "scored"
    MISSING_DIMENSIONS = "missing_dimensions"
    MISSING_RESULTS = "missing_results"
    NOT_SCORED = "not_scored"
    EXECUTION_FAILED = "execution_failed"


class OfficialScorerValidationError(RuntimeError):
    """Raised when a checkout cannot prove the pinned scorer contract."""


def _coerce_dimension(value: ParseBenchDimension | str) -> ParseBenchDimension:
    try:
        return (
            value
            if isinstance(value, ParseBenchDimension)
            else ParseBenchDimension(value)
        )
    except ValueError as exc:
        expected = ", ".join(dimension.value for dimension in PARSEBENCH_DIMENSIONS)
        raise ValueError(
            f"unknown ParseBench dimension {value!r}; expected one of: {expected}"
        ) from exc


def _coerce_nonnegative_count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _coerce_finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


def _coerce_score(value: object, field_name: str = "score") -> float:
    score = _coerce_finite_number(value, field_name)
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{field_name} must be between 0.0 and 1.0 inclusive")
    return score


def _coerce_aggregate_metrics(value: object) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        raise TypeError("aggregate_metrics must be a mapping")
    metrics: dict[str, float] = {}
    for metric_name, metric_value in value.items():
        if not isinstance(metric_name, str) or not metric_name:
            raise TypeError("aggregate metric names must be non-empty strings")
        metrics[metric_name] = _coerce_finite_number(
            metric_value,
            f"aggregate_metrics[{metric_name!r}]",
        )
    return MappingProxyType(metrics)


def _coerce_status(
    value: ParseBenchResultStatus | str,
) -> ParseBenchResultStatus:
    try:
        return (
            value
            if isinstance(value, ParseBenchResultStatus)
            else ParseBenchResultStatus(value)
        )
    except ValueError as exc:
        raise ValueError(f"unknown ParseBench result status: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class ParseBenchCaseResult:
    """Official scoring outcome for one case in one dimension.

    A source parsing task may yield multiple case results when it contributes
    to more than one ParseBench dimension. Only ``scored`` results contain a
    numeric value. All other states remain explicit inputs to global reduction.
    """

    case_id: str
    dimension: ParseBenchDimension
    status: ParseBenchResultStatus
    score: float | None = None
    primary_metric: str | None = None
    aggregate_metrics: Mapping[str, float] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id must be a non-empty string")
        object.__setattr__(self, "case_id", self.case_id.strip())
        dimension = _coerce_dimension(self.dimension)
        status = _coerce_status(self.status)
        object.__setattr__(self, "dimension", dimension)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "aggregate_metrics",
            _coerce_aggregate_metrics(self.aggregate_metrics),
        )
        object.__setattr__(
            self,
            "diagnostics",
            tuple(str(item).strip() for item in self.diagnostics if str(item).strip()),
        )

        expected_metric = PARSEBENCH_PRIMARY_METRICS[dimension]
        if self.primary_metric not in (None, expected_metric):
            raise ValueError(
                f"primary metric for {dimension.value!r} must be {expected_metric!r}, "
                f"not {self.primary_metric!r}"
            )
        object.__setattr__(self, "primary_metric", expected_metric)
        if status is ParseBenchResultStatus.SCORED:
            if self.score is None:
                raise ValueError("a scored ParseBench case result must contain a score")
            object.__setattr__(self, "score", _coerce_score(self.score))
        elif self.score is not None:
            raise ValueError(
                f"a {status.value} ParseBench case result cannot contain a score"
            )

    @classmethod
    def scored(
        cls,
        *,
        case_id: str,
        dimension: ParseBenchDimension | str,
        score: float,
        aggregate_metrics: Mapping[str, float] | None = None,
        diagnostics: Iterable[str] = (),
    ) -> ParseBenchCaseResult:
        return cls(
            case_id=case_id,
            dimension=_coerce_dimension(dimension),
            status=ParseBenchResultStatus.SCORED,
            score=score,
            aggregate_metrics=aggregate_metrics or {},
            diagnostics=tuple(diagnostics),
        )

    @classmethod
    def not_scored(
        cls,
        *,
        case_id: str,
        dimension: ParseBenchDimension | str,
        aggregate_metrics: Mapping[str, float] | None = None,
        diagnostics: Iterable[str] = (),
    ) -> ParseBenchCaseResult:
        return cls(
            case_id=case_id,
            dimension=_coerce_dimension(dimension),
            status=ParseBenchResultStatus.NOT_SCORED,
            aggregate_metrics=aggregate_metrics or {},
            diagnostics=tuple(diagnostics),
        )

    @classmethod
    def execution_failed(
        cls,
        *,
        case_id: str,
        dimension: ParseBenchDimension | str,
        diagnostics: Iterable[str] = (),
    ) -> ParseBenchCaseResult:
        return cls(
            case_id=case_id,
            dimension=_coerce_dimension(dimension),
            status=ParseBenchResultStatus.EXECUTION_FAILED,
            diagnostics=tuple(diagnostics),
        )

    @classmethod
    def missing(
        cls,
        *,
        case_id: str,
        dimension: ParseBenchDimension | str,
        diagnostics: Iterable[str] = (),
    ) -> ParseBenchCaseResult:
        return cls(
            case_id=case_id,
            dimension=_coerce_dimension(dimension),
            status=ParseBenchResultStatus.MISSING,
            diagnostics=tuple(diagnostics),
        )

    @property
    def score_percent(self) -> float | None:
        return None if self.score is None else self.score * 100.0

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ParseBenchCaseResult:
        """Validate a versioned case artifact returned by a worker/gateway."""

        if not isinstance(payload, Mapping):
            raise TypeError("ParseBench case result payload must be a mapping")
        if payload.get("schema") != PARSEBENCH_CASE_RESULT_SCHEMA:
            raise ValueError(
                "ParseBench case result schema must be "
                f"{PARSEBENCH_CASE_RESULT_SCHEMA!r}"
            )
        missing_fields = [
            field_name
            for field_name in ("case_id", "dimension", "status")
            if field_name not in payload
        ]
        if missing_fields:
            raise ValueError(
                "ParseBench case result is missing field(s): "
                + ", ".join(missing_fields)
            )
        diagnostics = payload.get("diagnostics", [])
        if not isinstance(diagnostics, list) or any(
            not isinstance(item, str) for item in diagnostics
        ):
            raise TypeError("ParseBench case diagnostics must be a list of strings")
        return cls(
            case_id=payload["case_id"],
            dimension=payload["dimension"],
            status=payload["status"],
            score=payload.get("score"),
            primary_metric=payload.get("primary_metric"),
            aggregate_metrics=payload.get("aggregate_metrics", {}),
            diagnostics=tuple(diagnostics),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible case artifact."""

        return {
            "schema": PARSEBENCH_CASE_RESULT_SCHEMA,
            "case_id": self.case_id,
            "dimension": self.dimension.value,
            "status": self.status.value,
            "score": self.score,
            "score_percent": self.score_percent,
            "primary_metric": self.primary_metric,
            "aggregate_metrics": dict(sorted(self.aggregate_metrics.items())),
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class ParseBenchDimensionResult:
    """Detailed official result for a single ParseBench dimension."""

    dimension: ParseBenchDimension
    status: ParseBenchResultStatus
    score: float | None = None
    diagnostic_score: float | None = None
    primary_metric: str | None = None
    total_examples: int = 0
    successful_examples: int = 0
    failed_examples: int = 0
    skipped_examples: int = 0
    numeric_examples: int = 0
    not_scored_examples: int = 0
    missing_examples: int = 0
    aggregate_metrics: Mapping[str, float] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    missing_case_ids: tuple[str, ...] = ()
    case_results: tuple[ParseBenchCaseResult, ...] = ()

    def __post_init__(self) -> None:
        dimension = _coerce_dimension(self.dimension)
        status = _coerce_status(self.status)

        object.__setattr__(self, "dimension", dimension)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "total_examples",
            _coerce_nonnegative_count(self.total_examples, "total_examples"),
        )
        object.__setattr__(
            self,
            "successful_examples",
            _coerce_nonnegative_count(self.successful_examples, "successful_examples"),
        )
        object.__setattr__(
            self,
            "failed_examples",
            _coerce_nonnegative_count(self.failed_examples, "failed_examples"),
        )
        object.__setattr__(
            self,
            "skipped_examples",
            _coerce_nonnegative_count(self.skipped_examples, "skipped_examples"),
        )
        object.__setattr__(
            self,
            "numeric_examples",
            _coerce_nonnegative_count(self.numeric_examples, "numeric_examples"),
        )
        object.__setattr__(
            self,
            "not_scored_examples",
            _coerce_nonnegative_count(
                self.not_scored_examples,
                "not_scored_examples",
            ),
        )
        object.__setattr__(
            self,
            "missing_examples",
            _coerce_nonnegative_count(self.missing_examples, "missing_examples"),
        )
        object.__setattr__(
            self, "aggregate_metrics", _coerce_aggregate_metrics(self.aggregate_metrics)
        )
        object.__setattr__(
            self, "errors", tuple(str(error) for error in self.errors if str(error))
        )
        object.__setattr__(
            self,
            "missing_case_ids",
            tuple(str(case_id) for case_id in self.missing_case_ids),
        )
        case_results = tuple(self.case_results)
        if any(not isinstance(result, ParseBenchCaseResult) for result in case_results):
            raise TypeError("case_results must contain ParseBenchCaseResult instances")
        if any(result.dimension is not dimension for result in case_results):
            raise ValueError("all case_results must belong to the dimension result")
        object.__setattr__(self, "case_results", case_results)

        expected_metric = PARSEBENCH_PRIMARY_METRICS[dimension]
        if self.primary_metric not in (None, expected_metric):
            raise ValueError(
                f"primary metric for {dimension.value!r} must be {expected_metric!r}, "
                f"not {self.primary_metric!r}"
            )
        object.__setattr__(self, "primary_metric", expected_metric)

        if status is ParseBenchResultStatus.SCORED:
            if self.score is None:
                raise ValueError("a scored ParseBench result must contain a score")
            object.__setattr__(self, "score", _coerce_score(self.score))
        elif self.score is not None:
            raise ValueError(
                f"a {status.value} ParseBench result cannot contain a score"
            )
        if self.diagnostic_score is None and self.score is not None:
            object.__setattr__(self, "diagnostic_score", self.score)
        elif self.diagnostic_score is not None:
            object.__setattr__(
                self,
                "diagnostic_score",
                _coerce_score(self.diagnostic_score, "diagnostic_score"),
            )

    @classmethod
    def scored(
        cls,
        *,
        dimension: ParseBenchDimension | str,
        score: float,
        total_examples: int = 0,
        successful_examples: int = 0,
        failed_examples: int = 0,
        skipped_examples: int = 0,
        aggregate_metrics: Mapping[str, float] | None = None,
        errors: Iterable[str] = (),
        numeric_examples: int | None = None,
        not_scored_examples: int | None = None,
        missing_examples: int = 0,
        case_results: Iterable[ParseBenchCaseResult] = (),
    ) -> ParseBenchDimensionResult:
        """Build a scored result; ``0.0`` remains a real score."""

        return cls(
            dimension=_coerce_dimension(dimension),
            status=ParseBenchResultStatus.SCORED,
            score=score,
            total_examples=total_examples,
            successful_examples=successful_examples,
            failed_examples=failed_examples,
            skipped_examples=skipped_examples,
            numeric_examples=(
                successful_examples if numeric_examples is None else numeric_examples
            ),
            not_scored_examples=(
                skipped_examples if not_scored_examples is None else not_scored_examples
            ),
            missing_examples=missing_examples,
            aggregate_metrics=aggregate_metrics or {},
            errors=tuple(errors),
            case_results=tuple(case_results),
        )

    @classmethod
    def not_scored(
        cls,
        *,
        dimension: ParseBenchDimension | str,
        total_examples: int = 0,
        successful_examples: int = 0,
        failed_examples: int = 0,
        skipped_examples: int = 0,
        aggregate_metrics: Mapping[str, float] | None = None,
        errors: Iterable[str] = (),
        numeric_examples: int = 0,
        not_scored_examples: int | None = None,
        missing_examples: int = 0,
        case_results: Iterable[ParseBenchCaseResult] = (),
    ) -> ParseBenchDimensionResult:
        """Build an explicitly unscored result, which is never treated as zero."""

        return cls(
            dimension=_coerce_dimension(dimension),
            status=ParseBenchResultStatus.NOT_SCORED,
            total_examples=total_examples,
            successful_examples=successful_examples,
            failed_examples=failed_examples,
            skipped_examples=skipped_examples,
            numeric_examples=numeric_examples,
            not_scored_examples=(
                skipped_examples if not_scored_examples is None else not_scored_examples
            ),
            missing_examples=missing_examples,
            aggregate_metrics=aggregate_metrics or {},
            errors=tuple(errors),
            case_results=tuple(case_results),
        )

    @classmethod
    def execution_failed(
        cls,
        *,
        dimension: ParseBenchDimension | str,
        total_examples: int = 0,
        successful_examples: int = 0,
        failed_examples: int = 1,
        skipped_examples: int = 0,
        aggregate_metrics: Mapping[str, float] | None = None,
        errors: Iterable[str] = (),
        diagnostic_score: float | None = None,
        numeric_examples: int = 0,
        not_scored_examples: int | None = None,
        missing_examples: int = 0,
        case_results: Iterable[ParseBenchCaseResult] = (),
    ) -> ParseBenchDimensionResult:
        """Build a scorer/execution failure, which is never fabricated as zero."""

        return cls(
            dimension=_coerce_dimension(dimension),
            status=ParseBenchResultStatus.EXECUTION_FAILED,
            diagnostic_score=diagnostic_score,
            total_examples=total_examples,
            successful_examples=successful_examples,
            failed_examples=failed_examples,
            skipped_examples=skipped_examples,
            numeric_examples=numeric_examples,
            not_scored_examples=(
                skipped_examples if not_scored_examples is None else not_scored_examples
            ),
            missing_examples=missing_examples,
            aggregate_metrics=aggregate_metrics or {},
            errors=tuple(errors),
            case_results=tuple(case_results),
        )

    @classmethod
    def missing(
        cls,
        *,
        dimension: ParseBenchDimension | str,
        total_examples: int = 0,
        successful_examples: int = 0,
        failed_examples: int = 0,
        skipped_examples: int = 0,
        diagnostic_score: float | None = None,
        numeric_examples: int = 0,
        not_scored_examples: int = 0,
        missing_examples: int = 1,
        missing_case_ids: Iterable[str] = (),
        aggregate_metrics: Mapping[str, float] | None = None,
        errors: Iterable[str] = (),
        case_results: Iterable[ParseBenchCaseResult] = (),
    ) -> ParseBenchDimensionResult:
        """Build a dimension with absent expected case results."""

        return cls(
            dimension=_coerce_dimension(dimension),
            status=ParseBenchResultStatus.MISSING,
            diagnostic_score=diagnostic_score,
            total_examples=total_examples,
            successful_examples=successful_examples,
            failed_examples=failed_examples,
            skipped_examples=skipped_examples,
            numeric_examples=numeric_examples,
            not_scored_examples=not_scored_examples,
            missing_examples=missing_examples,
            missing_case_ids=tuple(missing_case_ids),
            aggregate_metrics=aggregate_metrics or {},
            errors=tuple(errors),
            case_results=tuple(case_results),
        )

    @property
    def score_percent(self) -> float | None:
        return None if self.score is None else self.score * 100.0

    @property
    def counts(self) -> dict[str, int]:
        return {
            "numeric": self.numeric_examples,
            "not_scored": self.not_scored_examples,
            "failed": self.failed_examples,
            "missing": self.missing_examples,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible result artifact."""

        return {
            "dimension": self.dimension.value,
            "status": self.status.value,
            "score": self.score,
            "score_percent": self.score_percent,
            "diagnostic_score": self.diagnostic_score,
            "primary_metric": self.primary_metric,
            "total_examples": self.total_examples,
            "successful_examples": self.successful_examples,
            "failed_examples": self.failed_examples,
            "skipped_examples": self.skipped_examples,
            "counts": self.counts,
            "aggregate_metrics": dict(sorted(self.aggregate_metrics.items())),
            "errors": list(self.errors),
            "missing_case_ids": list(self.missing_case_ids),
            "case_results": [result.to_dict() for result in self.case_results],
        }


@dataclass(frozen=True, slots=True)
class ParseBenchReport:
    """Five-dimension ParseBench report with a fail-closed overall score."""

    status: ParseBenchOverallStatus
    dimensions: tuple[ParseBenchDimensionResult, ...]
    overall_score: float | None
    missing_dimensions: tuple[ParseBenchDimension, ...] = ()
    missing_result_dimensions: tuple[ParseBenchDimension, ...] = ()
    failed_dimensions: tuple[ParseBenchDimension, ...] = ()
    not_scored_dimensions: tuple[ParseBenchDimension, ...] = ()
    diagnostics: tuple[str, ...] = ()

    @property
    def overall_percent(self) -> float | None:
        return None if self.overall_score is None else self.overall_score * 100.0

    @property
    def publishable(self) -> bool:
        """Only complete official five-dimension results are publishable."""

        return self.status is ParseBenchOverallStatus.SCORED

    @property
    def counts(self) -> dict[str, int]:
        """Global case counts, kept separate from the score calculation."""

        return {
            key: sum(result.counts[key] for result in self.dimensions)
            for key in ("numeric", "not_scored", "failed", "missing")
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the versioned, JSON-compatible benchmark report."""

        return {
            "schema": PARSEBENCH_REPORT_SCHEMA,
            "dataset_id": PARSEBENCH_DATASET_ID,
            "data_revision": PARSEBENCH_DATA_REVISION,
            "scorer_repository": PARSEBENCH_SCORER_REPOSITORY,
            "scorer_revision": PARSEBENCH_SCORER_REVISION,
            "status": self.status.value,
            "publishable": self.publishable,
            "overall_score": self.overall_score,
            "overall_percent": self.overall_percent,
            "counts": self.counts,
            "missing_dimensions": [
                dimension.value for dimension in self.missing_dimensions
            ],
            "missing_result_dimensions": [
                dimension.value for dimension in self.missing_result_dimensions
            ],
            "failed_dimensions": [
                dimension.value for dimension in self.failed_dimensions
            ],
            "not_scored_dimensions": [
                dimension.value for dimension in self.not_scored_dimensions
            ],
            "diagnostics": list(self.diagnostics),
            "dimensions": [result.to_dict() for result in self.dimensions],
        }


def _official_count(report: Mapping[str, Any], key: str) -> int:
    if key not in report:
        raise ValueError(f"official ParseBench report is missing {key!r}")
    return _coerce_nonnegative_count(report[key], key)


def _official_errors(report: Mapping[str, Any]) -> tuple[str, ...]:
    raw_results = report.get("per_example_results", [])
    if raw_results is None:
        return ()
    if not isinstance(raw_results, list):
        raise TypeError("official ParseBench per_example_results must be a list")

    errors: list[str] = []
    for raw_result in raw_results:
        if (
            not isinstance(raw_result, Mapping)
            or raw_result.get("success") is not False
        ):
            continue
        error = raw_result.get("error")
        if not isinstance(error, str) or not error.strip():
            continue
        test_id = raw_result.get("test_id")
        prefix = f"{test_id}: " if isinstance(test_id, str) and test_id else ""
        errors.append(prefix + error.strip())
    return tuple(errors)


def _official_primary_counts(
    report: Mapping[str, Any],
    *,
    primary_metric: str,
    successful: int,
    skipped: int,
    has_aggregate_score: bool,
) -> tuple[int, int]:
    """Count numeric and inapplicable successes from detailed official rows."""

    raw_results = report.get("per_example_results")
    if not isinstance(raw_results, list) or not raw_results:
        if has_aggregate_score:
            return successful, skipped
        return 0, successful + skipped

    per_example_metric = primary_metric.removeprefix("avg_")
    numeric_rows = 0
    successful_rows = 0
    for raw_result in raw_results:
        if not isinstance(raw_result, Mapping) or raw_result.get("success") is not True:
            continue
        successful_rows += 1
        raw_metrics = raw_result.get("metrics", [])
        if not isinstance(raw_metrics, list):
            raise TypeError("official ParseBench per-example metrics must be a list")
        if any(
            isinstance(metric, Mapping)
            and metric.get("metric_name") == per_example_metric
            for metric in raw_metrics
        ):
            numeric_rows += 1

    # Official summaries normally retain every successful row. If an older or
    # compact report omits details, use aggregate-score presence as the only
    # safe classification for those unrepresented successes.
    unrepresented = max(0, successful - successful_rows)
    if has_aggregate_score:
        numeric = numeric_rows + unrepresented
    else:
        numeric = numeric_rows
    not_scored = successful_rows - numeric_rows + skipped
    if not has_aggregate_score:
        not_scored += unrepresented
    return numeric, not_scored


def dimension_result_from_official_report(
    dimension: ParseBenchDimension | str,
    report: Mapping[str, Any],
) -> ParseBenchDimensionResult:
    """Adapt one pinned official ``EvaluationSummary`` JSON document.

    Within-dimension aggregation is taken verbatim from the official scorer.
    The absence of its primary metric is classified explicitly rather than
    being converted to a numeric zero.
    """

    dimension = _coerce_dimension(dimension)
    if not isinstance(report, Mapping):
        raise TypeError("official ParseBench report must be a mapping")

    total = _official_count(report, "total_examples")
    successful = _official_count(report, "successful")
    failed = _official_count(report, "failed")
    skipped = _official_count(report, "skipped")
    aggregate_metrics = _coerce_aggregate_metrics(report.get("aggregate_metrics"))
    errors = _official_errors(report)
    primary_metric = PARSEBENCH_PRIMARY_METRICS[dimension]
    numeric_count, not_scored_count = _official_primary_counts(
        report,
        primary_metric=primary_metric,
        successful=successful,
        skipped=skipped,
        has_aggregate_score=primary_metric in aggregate_metrics,
    )
    if primary_metric not in aggregate_metrics and numeric_count:
        raise ValueError(
            f"official ParseBench report contains {numeric_count} per-example "
            f"{primary_metric.removeprefix('avg_')!r} metric(s) but no {primary_metric!r} aggregate"
        )

    if primary_metric in aggregate_metrics:
        return ParseBenchDimensionResult.scored(
            dimension=dimension,
            score=_coerce_score(aggregate_metrics[primary_metric], primary_metric),
            total_examples=total,
            successful_examples=successful,
            failed_examples=failed,
            skipped_examples=skipped,
            numeric_examples=numeric_count,
            not_scored_examples=not_scored_count,
            aggregate_metrics=aggregate_metrics,
            errors=errors,
        )

    if failed:
        if not errors:
            errors = (
                f"official scorer reported {failed} failed example(s) without a primary score",
            )
        return ParseBenchDimensionResult.execution_failed(
            dimension=dimension,
            total_examples=total,
            successful_examples=successful,
            failed_examples=failed,
            skipped_examples=skipped,
            numeric_examples=numeric_count,
            not_scored_examples=not_scored_count,
            aggregate_metrics=aggregate_metrics,
            errors=errors,
        )

    return ParseBenchDimensionResult.not_scored(
        dimension=dimension,
        total_examples=total,
        successful_examples=successful,
        failed_examples=failed,
        skipped_examples=skipped,
        numeric_examples=numeric_count,
        not_scored_examples=not_scored_count,
        aggregate_metrics=aggregate_metrics,
        errors=errors,
    )


def _normalize_expected_case_ids(
    expected_case_ids: Mapping[ParseBenchDimension | str, Iterable[str]],
) -> dict[ParseBenchDimension, tuple[str, ...]]:
    normalized: dict[ParseBenchDimension, tuple[str, ...]] = {}
    for raw_dimension, raw_case_ids in expected_case_ids.items():
        dimension = _coerce_dimension(raw_dimension)
        if dimension in normalized:
            raise ValueError(f"duplicate expected dimension: {dimension.value}")
        if isinstance(raw_case_ids, (str, bytes)):
            raise TypeError(
                "expected case IDs must be an iterable of strings, not a string"
            )
        case_ids: list[str] = []
        seen: set[str] = set()
        for raw_case_id in raw_case_ids:
            if not isinstance(raw_case_id, str) or not raw_case_id.strip():
                raise ValueError("expected case IDs must be non-empty strings")
            case_id = raw_case_id.strip()
            if case_id in seen:
                raise ValueError(
                    f"duplicate expected ParseBench case for {dimension.value}: {case_id}"
                )
            seen.add(case_id)
            case_ids.append(case_id)
        normalized[dimension] = tuple(case_ids)
    return normalized


def _case_diagnostic(result: ParseBenchCaseResult, fallback: str) -> str:
    detail = "; ".join(result.diagnostics) if result.diagnostics else fallback
    return f"{result.case_id}: {detail}"


def _aggregate_case_dimension(
    dimension: ParseBenchDimension,
    case_results: list[ParseBenchCaseResult],
    inferred_missing_ids: tuple[str, ...],
) -> ParseBenchDimensionResult:
    ordered_cases = tuple(sorted(case_results, key=lambda result: result.case_id))
    numeric = tuple(
        result
        for result in ordered_cases
        if result.status is ParseBenchResultStatus.SCORED
    )
    not_scored = tuple(
        result
        for result in ordered_cases
        if result.status is ParseBenchResultStatus.NOT_SCORED
    )
    failed = tuple(
        result
        for result in ordered_cases
        if result.status is ParseBenchResultStatus.EXECUTION_FAILED
    )
    explicit_missing = tuple(
        result
        for result in ordered_cases
        if result.status is ParseBenchResultStatus.MISSING
    )
    missing_ids = tuple(result.case_id for result in explicit_missing) + tuple(
        sorted(inferred_missing_ids)
    )
    numeric_scores = [result.score for result in numeric]
    if any(score is None for score in numeric_scores):  # defensive invariant guard
        raise ValueError("numeric ParseBench case results must contain scores")
    diagnostic_score = (
        math.fsum(score for score in numeric_scores if score is not None)
        / len(numeric_scores)
        if numeric_scores
        else None
    )
    primary_metric = PARSEBENCH_PRIMARY_METRICS[dimension]
    aggregate_metrics = (
        {primary_metric: diagnostic_score} if diagnostic_score is not None else {}
    )
    error_details = tuple(
        _case_diagnostic(result, "execution failed") for result in failed
    )
    total = len(ordered_cases) + len(inferred_missing_ids)
    common: dict[str, Any] = {
        "dimension": dimension,
        "total_examples": total,
        "successful_examples": len(numeric),
        "failed_examples": len(failed),
        "skipped_examples": len(not_scored),
        "numeric_examples": len(numeric),
        "not_scored_examples": len(not_scored),
        "missing_examples": len(missing_ids),
        "aggregate_metrics": aggregate_metrics,
        "errors": error_details,
        "case_results": ordered_cases,
    }

    if failed:
        return ParseBenchDimensionResult.execution_failed(
            diagnostic_score=diagnostic_score,
            **common,
        )
    if missing_ids:
        return ParseBenchDimensionResult.missing(
            diagnostic_score=diagnostic_score,
            missing_case_ids=missing_ids,
            **common,
        )
    if numeric:
        if diagnostic_score is None:  # narrowed by ``numeric`` at runtime
            raise ValueError("numeric ParseBench case results must produce a mean")
        return ParseBenchDimensionResult.scored(
            score=diagnostic_score,
            **common,
        )
    return ParseBenchDimensionResult.not_scored(**common)


def reduce_parsebench_case_results(
    results: Iterable[ParseBenchCaseResult],
    *,
    expected_case_ids: Mapping[ParseBenchDimension | str, Iterable[str]] | None = None,
) -> ParseBenchReport:
    """Aggregate detailed case outcomes, then perform the official reduction.

    Numeric case scores are averaged within their dimension. ``not_scored``
    cases remain in the report but are excluded from that denominator. Any
    execution failure or missing expected result blocks publication while a
    partial numeric mean is retained only as ``diagnostic_score``.

    Supplying ``expected_case_ids`` lets the collector prove completeness
    against its immutable benchmark manifest instead of assuming that every
    returned artifact represents every scheduled case.
    """

    expected = (
        _normalize_expected_case_ids(expected_case_ids)
        if expected_case_ids is not None
        else None
    )
    by_dimension: dict[ParseBenchDimension, list[ParseBenchCaseResult]] = {
        dimension: [] for dimension in PARSEBENCH_DIMENSIONS
    }
    observed: set[tuple[ParseBenchDimension, str]] = set()
    for result in results:
        if not isinstance(result, ParseBenchCaseResult):
            raise TypeError(
                "case reduction accepts only ParseBenchCaseResult instances; "
                "raw task rewards are not official ParseBench inputs"
            )
        key = (result.dimension, result.case_id)
        if key in observed:
            raise ValueError(
                f"duplicate ParseBench case for {result.dimension.value}: {result.case_id}"
            )
        observed.add(key)
        if expected is not None and result.case_id not in expected.get(
            result.dimension, ()
        ):
            raise ValueError(
                f"ParseBench case {result.dimension.value}/{result.case_id} "
                "is not present in the expected manifest"
            )
        by_dimension[result.dimension].append(result)

    dimension_results: list[ParseBenchDimensionResult] = []
    for dimension in PARSEBENCH_DIMENSIONS:
        cases = by_dimension[dimension]
        inferred_missing: tuple[str, ...] = ()
        if expected is not None:
            inferred_missing = tuple(
                case_id
                for case_id in expected.get(dimension, ())
                if (dimension, case_id) not in observed
            )
        if not cases and not inferred_missing:
            continue
        dimension_results.append(
            _aggregate_case_dimension(dimension, cases, inferred_missing)
        )

    return reduce_parsebench_results(dimension_results)


def reduce_parsebench_results(
    results: Iterable[ParseBenchDimensionResult],
) -> ParseBenchReport:
    """Reduce official dimension results using ParseBench leaderboard semantics.

    The overall score is the equal-weight arithmetic mean of exactly five
    dimension scores. A partial, failed, or explicitly unscored dimension
    never produces a misleading partial overall score.
    """

    by_dimension: dict[ParseBenchDimension, ParseBenchDimensionResult] = {}
    for result in results:
        if not isinstance(result, ParseBenchDimensionResult):
            raise TypeError("results must contain ParseBenchDimensionResult instances")
        if result.dimension in by_dimension:
            raise ValueError(
                f"duplicate ParseBench dimension: {result.dimension.value}"
            )
        by_dimension[result.dimension] = result

    ordered_results = tuple(
        by_dimension[dimension]
        for dimension in PARSEBENCH_DIMENSIONS
        if dimension in by_dimension
    )
    missing = tuple(
        dimension
        for dimension in PARSEBENCH_DIMENSIONS
        if dimension not in by_dimension
    )
    failed = tuple(
        result.dimension
        for result in ordered_results
        if result.status is ParseBenchResultStatus.EXECUTION_FAILED
    )
    missing_results = tuple(
        result.dimension
        for result in ordered_results
        if result.status is ParseBenchResultStatus.MISSING
        or result.missing_examples > 0
    )
    not_scored = tuple(
        result.dimension
        for result in ordered_results
        if result.status is ParseBenchResultStatus.NOT_SCORED
    )

    diagnostics: list[str] = []
    if missing:
        diagnostics.append(
            "missing dimensions: " + ", ".join(dimension.value for dimension in missing)
        )
    for result in ordered_results:
        if result.failed_examples:
            diagnostics.append(
                f"{result.dimension.value}: {result.failed_examples} execution failure(s)"
            )
        if result.missing_examples:
            diagnostics.append(
                f"{result.dimension.value}: {result.missing_examples} missing result(s)"
            )
        if (
            result.status is ParseBenchResultStatus.NOT_SCORED
            and result.not_scored_examples
        ):
            diagnostics.append(
                f"{result.dimension.value}: no numeric case scores "
                f"({result.not_scored_examples} not_scored)"
            )

    if failed:
        status = ParseBenchOverallStatus.EXECUTION_FAILED
        overall_score = None
    elif missing_results:
        status = ParseBenchOverallStatus.MISSING_RESULTS
        overall_score = None
    elif missing:
        status = ParseBenchOverallStatus.MISSING_DIMENSIONS
        overall_score = None
    elif not_scored:
        status = ParseBenchOverallStatus.NOT_SCORED
        overall_score = None
    else:
        status = ParseBenchOverallStatus.SCORED
        scores = [result.score for result in ordered_results]
        if any(score is None for score in scores):  # defensive invariant guard
            raise ValueError("all scored dimensions must contain a score")
        overall_score = math.fsum(score for score in scores if score is not None) / len(
            PARSEBENCH_DIMENSIONS
        )

    return ParseBenchReport(
        status=status,
        dimensions=ordered_results,
        overall_score=overall_score,
        missing_dimensions=missing,
        missing_result_dimensions=missing_results,
        failed_dimensions=failed,
        not_scored_dimensions=not_scored,
        diagnostics=tuple(diagnostics),
    )


_SCORER_PROBE = r"""
import json
import sys
from pathlib import Path

import parse_bench
from parse_bench.analysis.aggregation_report import _DEFAULT_METRICS
from parse_bench.evaluation.cli import EvaluationCLI
from parse_bench.schemas.evaluation import EvaluationSummary

print(json.dumps({
    "module_file": str(Path(parse_bench.__file__).resolve()),
    "python": list(sys.version_info[:3]),
    "default_metrics": _DEFAULT_METRICS,
    "has_evaluation_cli": callable(getattr(EvaluationCLI, "run", None)),
    "has_evaluation_summary": callable(getattr(EvaluationSummary, "model_validate", None)),
}, sort_keys=True))
"""


def _resolve_executable(executable: str | os.PathLike[str]) -> Path:
    value = os.fspath(executable)
    located = shutil.which(value)
    # Do not resolve the final symlink: virtualenv launchers commonly point at
    # a base interpreter, and resolving it would discard the venv's sys.prefix
    # and installed scorer dependencies.
    path = Path(
        os.path.abspath(Path(located if located is not None else value).expanduser())
    )
    if not path.is_file():
        raise OfficialScorerValidationError(f"Python executable does not exist: {path}")
    return path


def _completed_process(
    command: list[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=None if env is None else dict(env),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OfficialScorerValidationError(
            f"could not execute scorer validation command: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class OfficialScorerEnvironment:
    """A validated process boundary for the pinned upstream scorer."""

    checkout: Path
    source_root: Path
    python_executable: Path
    scorer_revision: str = field(default=PARSEBENCH_SCORER_REVISION, init=False)
    repository: str = field(default=PARSEBENCH_SCORER_REPOSITORY, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "checkout", Path(self.checkout))
        object.__setattr__(self, "source_root", Path(self.source_root))
        object.__setattr__(self, "python_executable", Path(self.python_executable))

    def subprocess_environment(
        self,
        base_environment: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Build the environment that imports only this checkout first."""

        environment = dict(os.environ if base_environment is None else base_environment)
        existing_pythonpath = environment.get("PYTHONPATH")
        pythonpath = str(self.source_root)
        if existing_pythonpath:
            pythonpath += os.pathsep + existing_pythonpath
        environment["PYTHONPATH"] = pythonpath
        # The pinned scorer is deterministic by default; make the optional
        # chart LLM normalization setting explicit at the process boundary.
        environment["LLAMACLOUD_BENCH_LLM_NORMALIZATION"] = "off"
        return environment

    def evaluation_command(
        self,
        *,
        dimension: ParseBenchDimension | str,
        output_dir: str | os.PathLike[str],
        test_cases_dir: str | os.PathLike[str],
        report_dir: str | os.PathLike[str],
        max_workers: int = 1,
    ) -> tuple[str, ...]:
        """Build the official scorer CLI command without using a shell."""

        dimension = _coerce_dimension(dimension)
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers < 1
        ):
            raise ValueError("max_workers must be a positive integer")
        return (
            str(self.python_executable),
            "-m",
            "parse_bench.cli",
            "evaluation",
            "run",
            f"--output_dir={Path(output_dir)}",
            f"--test_cases_dir={Path(test_cases_dir)}",
            f"--group={dimension.value}",
            f"--report_dir={Path(report_dir)}",
            "--export_csv=False",
            "--export_rule_csv=False",
            "--export_markdown=False",
            "--export_html=False",
            "--force=True",
            f"--max_workers={max_workers}",
        )


def validate_official_scorer(
    checkout: str | os.PathLike[str],
    *,
    python_executable: str | os.PathLike[str] = sys.executable,
) -> OfficialScorerEnvironment:
    """Validate an exact, clean official checkout and its Python environment."""

    checkout_path = Path(checkout).expanduser().resolve()
    source_root = (checkout_path / "src").resolve()
    package_root = source_root / "parse_bench"
    if not checkout_path.is_dir() or not (checkout_path / ".git").exists():
        raise OfficialScorerValidationError(
            f"not a ParseBench git checkout: {checkout_path}"
        )
    if not package_root.is_dir():
        raise OfficialScorerValidationError(
            f"official scorer package is missing: {package_root}"
        )
    resolved_python = _resolve_executable(python_executable)

    revision_process = _completed_process(
        ["git", "-C", str(checkout_path), "rev-parse", "--verify", "HEAD"],
        timeout=_PROBE_TIMEOUT_SECONDS,
    )
    if revision_process.returncode != 0:
        raise OfficialScorerValidationError(
            "could not read official scorer git revision"
        )
    actual_revision = revision_process.stdout.strip()
    if actual_revision != PARSEBENCH_SCORER_REVISION:
        raise OfficialScorerValidationError(
            f"official scorer revision mismatch: expected {PARSEBENCH_SCORER_REVISION}, "
            f"found {actual_revision or '<empty>'}"
        )

    status_process = _completed_process(
        [
            "git",
            "-C",
            str(checkout_path),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        timeout=_PROBE_TIMEOUT_SECONDS,
    )
    if status_process.returncode != 0:
        raise OfficialScorerValidationError(
            "could not inspect official scorer checkout cleanliness"
        )
    if status_process.stdout.strip():
        raise OfficialScorerValidationError(
            "official scorer checkout has uncommitted files"
        )

    environment = OfficialScorerEnvironment(
        checkout=checkout_path,
        source_root=source_root,
        python_executable=resolved_python,
    )
    probe_process = _completed_process(
        [str(resolved_python), "-c", _SCORER_PROBE],
        cwd=checkout_path,
        env=environment.subprocess_environment(),
        timeout=_PROBE_TIMEOUT_SECONDS,
    )
    if probe_process.returncode != 0:
        detail = _process_detail(probe_process)
        raise OfficialScorerValidationError(
            f"official scorer Python environment probe failed: {detail}"
        )

    try:
        lines = [line for line in probe_process.stdout.splitlines() if line.strip()]
        probe = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError, TypeError) as exc:
        raise OfficialScorerValidationError(
            "official scorer environment probe returned invalid JSON"
        ) from exc
    if not isinstance(probe, Mapping):
        raise OfficialScorerValidationError(
            "official scorer environment probe returned an invalid payload"
        )

    try:
        imported_module = Path(str(probe["module_file"])).resolve()
        python_version = tuple(probe["python"][:2])
    except (KeyError, TypeError) as exc:
        raise OfficialScorerValidationError(
            "official scorer environment probe is incomplete"
        ) from exc
    if not imported_module.is_relative_to(source_root):
        raise OfficialScorerValidationError(
            f"Python imported parse_bench outside the pinned checkout: {imported_module}"
        )
    if python_version < _MINIMUM_SCORER_PYTHON:
        raise OfficialScorerValidationError(
            "official scorer requires Python "
            f"{_MINIMUM_SCORER_PYTHON[0]}.{_MINIMUM_SCORER_PYTHON[1]} or newer"
        )
    defaults = probe.get("default_metrics")
    if not isinstance(defaults, Mapping) or any(
        defaults.get(group) != metric
        for group, metric in _UPSTREAM_DEFAULT_METRICS.items()
    ):
        raise OfficialScorerValidationError(
            "official scorer primary metric contract does not match the pinned revision"
        )
    if (
        probe.get("has_evaluation_cli") is not True
        or probe.get("has_evaluation_summary") is not True
    ):
        raise OfficialScorerValidationError(
            "official scorer evaluation API is incomplete"
        )
    return environment


def _process_detail(process: subprocess.CompletedProcess[str]) -> str:
    detail = (process.stderr or process.stdout or "no diagnostic output").strip()
    if len(detail) > _ERROR_TEXT_LIMIT:
        detail = detail[:_ERROR_TEXT_LIMIT] + "..."
    return detail


def run_official_scorer(
    environment: OfficialScorerEnvironment,
    *,
    dimension: ParseBenchDimension | str,
    output_dir: str | os.PathLike[str],
    test_cases_dir: str | os.PathLike[str],
    report_dir: str | os.PathLike[str],
    max_workers: int = 1,
    timeout_seconds: float = 8 * 60,
) -> ParseBenchDimensionResult:
    """Invoke the pinned scorer and adapt its generated dimension report.

    Process and report failures are returned as ``execution_failed`` results so
    callers cannot accidentally average them as either zeros or skips.
    Configuration errors (such as missing input directories) fail immediately.
    """

    if not isinstance(environment, OfficialScorerEnvironment):
        raise TypeError("environment must be an OfficialScorerEnvironment")
    dimension = _coerce_dimension(dimension)
    inference_path = Path(output_dir)
    cases_path = Path(test_cases_dir)
    report_path = Path(report_dir)
    if not inference_path.is_dir():
        raise FileNotFoundError(
            f"official scorer output directory does not exist: {inference_path}"
        )
    if not cases_path.is_dir():
        raise FileNotFoundError(
            f"official scorer test cases directory does not exist: {cases_path}"
        )
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive")

    command = list(
        environment.evaluation_command(
            dimension=dimension,
            output_dir=inference_path,
            test_cases_dir=cases_path,
            report_dir=report_path,
            max_workers=max_workers,
        )
    )
    try:
        process = subprocess.run(
            command,
            cwd=environment.checkout,
            env=environment.subprocess_environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=float(timeout_seconds),
        )
    except subprocess.TimeoutExpired:
        return ParseBenchDimensionResult.execution_failed(
            dimension=dimension,
            failed_examples=0,
            errors=(f"official scorer timed out after {timeout_seconds:g} seconds",),
        )
    except OSError as exc:
        return ParseBenchDimensionResult.execution_failed(
            dimension=dimension,
            failed_examples=0,
            errors=(f"official scorer could not start: {exc}",),
        )

    if process.returncode != 0:
        return ParseBenchDimensionResult.execution_failed(
            dimension=dimension,
            failed_examples=0,
            errors=(
                f"official scorer exited with code {process.returncode}: {_process_detail(process)}",
            ),
        )

    result_path = report_path / _OFFICIAL_REPORT_FILENAME
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise TypeError("official report root must be a JSON object")
        return dimension_result_from_official_report(dimension, payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return ParseBenchDimensionResult.execution_failed(
            dimension=dimension,
            failed_examples=0,
            errors=(f"official scorer produced an invalid report: {exc}",),
        )
