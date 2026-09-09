from __future__ import annotations

from aworld.self_evolve.gates import EvaluationRuntimeHealthGate
from aworld.self_evolve.runtime_health import (
    EvaluationRuntimeHealthStatus,
    assess_evaluation_runtime_health,
)
from aworld.self_evolve.types import EvaluationSummary


def _summary(**metrics: object) -> EvaluationSummary:
    return EvaluationSummary(
        variant_id="candidate",
        dataset_split="validation",
        metrics=metrics,
    )


def test_runtime_health_blocks_all_failed_judge_attempts() -> None:
    health = assess_evaluation_runtime_health(
        (
            _summary(
                evaluation_agent_signal=False,
                judge_attempt_count=3,
                judge_success_count=0,
                judge_failure_count=3,
            ),
        )
    )

    assert health.status is EvaluationRuntimeHealthStatus.UNHEALTHY
    assert health.blocks_candidate_attribution is True
    gate = EvaluationRuntimeHealthGate().evaluate(
        (
            _summary(
                evaluation_agent_signal=False,
                judge_attempt_count=3,
                judge_success_count=0,
                judge_failure_count=3,
            ),
        )
    )
    assert gate.passed is False
    assert gate.details is not None
    assert gate.details["failure_owner"] == "infrastructure"


def test_runtime_health_keeps_partial_success_as_degraded_but_usable() -> None:
    health = assess_evaluation_runtime_health(
        (
            _summary(
                evaluation_agent_signal=True,
                judge_attempt_count=3,
                judge_success_count=2,
                judge_failure_count=1,
                judge_timeout_count=1,
            ),
        )
    )

    assert health.status is EvaluationRuntimeHealthStatus.DEGRADED
    assert health.blocks_candidate_attribution is False


def test_runtime_health_is_backward_compatible_without_telemetry() -> None:
    health = assess_evaluation_runtime_health((_summary(score=90.0),))

    assert health.status is EvaluationRuntimeHealthStatus.UNKNOWN
    assert EvaluationRuntimeHealthGate().evaluate(
        (_summary(score=90.0),)
    ).passed
