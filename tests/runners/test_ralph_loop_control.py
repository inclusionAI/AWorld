import pytest

from aworld.runners.ralph.loop_control import (
    AcceptanceEvidence,
    ExecutionLimits,
    LoopDisposition,
    decide_after_attempt,
)


def test_verified_semantic_success_completes_goal() -> None:
    decision = decide_after_attempt(
        AcceptanceEvidence(
            semantic_succeeded=True,
            completion_claimed=True,
            verification_required=True,
            verification_passed=True,
        ),
        current_iteration=3,
        limits=ExecutionLimits(max_iterations=3),
    )

    assert decision.disposition is LoopDisposition.COMPLETE
    assert decision.reason == "acceptance_satisfied"


def test_missing_completion_claim_continues_before_limit() -> None:
    decision = decide_after_attempt(
        AcceptanceEvidence(
            semantic_succeeded=True,
            completion_claimed=False,
        ),
        current_iteration=2,
        limits=ExecutionLimits(max_iterations=3),
    )

    assert decision.disposition is LoopDisposition.CONTINUE
    assert decision.reason == "completion_claim_missing"


def test_failed_verification_continues_when_budget_remains() -> None:
    decision = decide_after_attempt(
        AcceptanceEvidence(
            semantic_succeeded=True,
            completion_claimed=True,
            verification_required=True,
            verification_passed=False,
        ),
        current_iteration=1,
        limits=ExecutionLimits(max_iterations=2),
    )

    assert decision.disposition is LoopDisposition.CONTINUE
    assert decision.reason == "verification_failed"


def test_limit_is_a_halt_not_success() -> None:
    decision = decide_after_attempt(
        AcceptanceEvidence(semantic_succeeded=False),
        current_iteration=2,
        limits=ExecutionLimits(max_iterations=2),
    )

    assert decision.disposition is LoopDisposition.LIMIT_REACHED
    assert decision.reason == "iteration_limit_reached"
    assert decision.acceptance_satisfied is False


def test_unbounded_limit_never_halts_an_incomplete_attempt() -> None:
    decision = decide_after_attempt(
        AcceptanceEvidence(semantic_succeeded=False),
        current_iteration=50_000,
        limits=ExecutionLimits(),
    )

    assert decision.disposition is LoopDisposition.CONTINUE


@pytest.mark.parametrize("value", (-1, True, 1.5))
def test_execution_limits_reject_invalid_iteration_limits(value) -> None:
    with pytest.raises((TypeError, ValueError)):
        ExecutionLimits(max_iterations=value)
