from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from aworld.self_evolve.types import EvaluationSummary


class EvaluationRuntimeHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvaluationRuntimeHealth:
    """Typed execution health, independent from candidate quality."""

    status: EvaluationRuntimeHealthStatus
    summary_count: int
    judge_attempt_count: int
    judge_success_count: int
    judge_failure_count: int
    judge_timeout_count: int
    unhealthy_summary_count: int
    reason_codes: tuple[str, ...] = ()

    @property
    def blocks_candidate_attribution(self) -> bool:
        return self.status is EvaluationRuntimeHealthStatus.UNHEALTHY

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "summary_count": self.summary_count,
            "judge_attempt_count": self.judge_attempt_count,
            "judge_success_count": self.judge_success_count,
            "judge_failure_count": self.judge_failure_count,
            "judge_timeout_count": self.judge_timeout_count,
            "unhealthy_summary_count": self.unhealthy_summary_count,
            "reason_codes": list(self.reason_codes),
        }


def assess_evaluation_runtime_health(
    summaries: Iterable[EvaluationSummary],
) -> EvaluationRuntimeHealth:
    """Separate evaluator/judge availability from scored candidate behavior.

    Missing telemetry is fail-compatible for custom or legacy evaluators. A
    summary is unhealthy only when it explicitly reports no evaluation-agent
    signal, all attempted judge calls failed, or all observed judge calls timed
    out. Partial failures are degraded and remain evaluable.
    """

    items = tuple(summaries)
    attempts = 0
    successes = 0
    failures = 0
    timeouts = 0
    unhealthy_count = 0
    observed = False
    reasons: set[str] = set()
    for summary in items:
        metrics = summary.metrics
        summary_attempts = _metric_count(
            metrics,
            "judge_attempt_count",
            fallback_key="judge_call_count",
        )
        summary_successes = _metric_count(metrics, "judge_success_count")
        summary_failures = _metric_count(metrics, "judge_failure_count")
        summary_timeouts = _metric_count(metrics, "judge_timeout_count")
        signal = metrics.get("evaluation_agent_signal")
        if (
            summary_attempts
            or summary_successes
            or summary_failures
            or summary_timeouts
            or isinstance(signal, bool)
        ):
            observed = True
        attempts += summary_attempts
        successes += summary_successes
        failures += summary_failures
        timeouts += summary_timeouts

        summary_unhealthy = False
        if signal is False:
            reasons.add("evaluation_agent_signal_missing")
            summary_unhealthy = True
        if summary_attempts > 0 and summary_successes == 0:
            reasons.add("judge_attempts_without_success")
            summary_unhealthy = True
        if summary_attempts > 0 and summary_timeouts >= summary_attempts:
            reasons.add("judge_calls_all_timed_out")
            summary_unhealthy = True
        if summary_unhealthy:
            unhealthy_count += 1

    if unhealthy_count:
        status = EvaluationRuntimeHealthStatus.UNHEALTHY
    elif failures or timeouts:
        status = EvaluationRuntimeHealthStatus.DEGRADED
        if failures:
            reasons.add("partial_judge_failures")
        if timeouts:
            reasons.add("partial_judge_timeouts")
    elif observed:
        status = EvaluationRuntimeHealthStatus.HEALTHY
    else:
        status = EvaluationRuntimeHealthStatus.UNKNOWN
        reasons.add("runtime_health_telemetry_unavailable")

    return EvaluationRuntimeHealth(
        status=status,
        summary_count=len(items),
        judge_attempt_count=attempts,
        judge_success_count=successes,
        judge_failure_count=failures,
        judge_timeout_count=timeouts,
        unhealthy_summary_count=unhealthy_count,
        reason_codes=tuple(sorted(reasons)),
    )


def _metric_count(
    metrics: Mapping[str, Any],
    key: str,
    *,
    fallback_key: str | None = None,
) -> int:
    value = metrics.get(key)
    if value is None and fallback_key is not None:
        value = metrics.get(fallback_key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))
