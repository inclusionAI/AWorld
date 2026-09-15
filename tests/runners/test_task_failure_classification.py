import pytest

from aworld.core.context.generation_budget import (
    GenerationBudgetExceeded,
    GenerationBudgetReceipt,
    GenerationPhase,
    GenerationStopReason,
)
from aworld.core.exceptions import AWorldRuntimeException
from aworld.core.task import TaskFailureOrigin, TaskResponse
from aworld.runners.event_runner import classify_task_exception


def _budget_error(reason: GenerationStopReason) -> GenerationBudgetExceeded:
    return GenerationBudgetExceeded(
        GenerationBudgetReceipt(
            reason=reason,
            phase=GenerationPhase.PRIMARY,
            elapsed_seconds=1.0,
            phase_elapsed_seconds=1.0,
            partial_response_available=False,
            partial_response_chars=0,
            tool_call_count=0,
            repair_attempted=False,
        )
    )


@pytest.mark.parametrize(
    "reason",
    [
        GenerationStopReason.ACTIVE_STREAM_OVER_BUDGET,
        GenerationStopReason.ACTION_REPAIR_EXHAUSTED,
    ],
)
def test_agent_generation_budget_failures_are_task_outcomes(
    reason: GenerationStopReason,
) -> None:
    evidence = classify_task_exception(_budget_error(reason))

    assert evidence == {
        "origin": TaskFailureOrigin.TASK.value,
        "code": reason.value,
        "error_type": "GenerationBudgetExceeded",
    }


@pytest.mark.parametrize(
    "reason",
    [
        GenerationStopReason.PROVIDER_TIMEOUT,
        GenerationStopReason.PROVIDER_CANCELLED,
        GenerationStopReason.STREAM_IDLE_TIMEOUT,
        GenerationStopReason.CALL_DEADLINE_EXCEEDED,
        GenerationStopReason.ACTION_REPAIR_TIMEOUT,
    ],
)
def test_provider_and_liveness_budget_failures_are_infrastructure(
    reason: GenerationStopReason,
) -> None:
    evidence = classify_task_exception(_budget_error(reason))

    assert evidence["origin"] == TaskFailureOrigin.INFRASTRUCTURE.value
    assert evidence["code"] == reason.value


def test_caller_generation_cancellation_stays_distinct() -> None:
    evidence = classify_task_exception(
        _budget_error(GenerationStopReason.CALLER_CANCELLED)
    )

    assert evidence["origin"] == TaskFailureOrigin.CANCELLED.value


def test_unknown_runtime_exception_fails_closed_as_infrastructure() -> None:
    evidence = classify_task_exception(RuntimeError("sensitive message"))

    assert evidence == {
        "origin": TaskFailureOrigin.INFRASTRUCTURE.value,
        "code": "runtime_exception",
        "error_type": "RuntimeError",
    }
    assert "sensitive" not in repr(evidence)


def test_wrapped_generation_failure_retains_typed_cause() -> None:
    cause = _budget_error(GenerationStopReason.ACTION_REPAIR_EXHAUSTED)
    try:
        raise cause
    except GenerationBudgetExceeded as exc:
        wrapped = AWorldRuntimeException("legacy wrapper")
        wrapped.__cause__ = exc

    evidence = classify_task_exception(wrapped)

    assert evidence["origin"] == TaskFailureOrigin.TASK.value
    assert evidence["code"] == GenerationStopReason.ACTION_REPAIR_EXHAUSTED.value
    assert evidence["error_type"] == "GenerationBudgetExceeded"


def test_task_response_serializes_failure_control_plane() -> None:
    response = TaskResponse(
        success=False,
        failure_origin=TaskFailureOrigin.INFRASTRUCTURE.value,
        failure_code="provider_timeout",
        error_type="GenerationBudgetExceeded",
    )

    payload = response.to_dict()

    assert payload["failure_origin"] == "infrastructure"
    assert payload["failure_code"] == "provider_timeout"
    assert payload["error_type"] == "GenerationBudgetExceeded"
