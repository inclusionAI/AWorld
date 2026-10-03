from aworld_cli.run_outcome import (
    DirectRunOutcome,
    DirectRunStatus,
    coerce_direct_run_outcome,
)


def _runtime_exception_summary(*, status_key: str = "semantic_status") -> dict:
    return {
        "results": [
            {
                "success": False,
                status_key: "incomplete",
                "completion_reason": "runtime_exception",
                "failure_origin": "task",
                "failure_code": "runtime_exception",
                "error_type": "LLMResponseError",
            }
        ]
    }


def test_runtime_exception_summary_cannot_be_promoted_to_success() -> None:
    summary = _runtime_exception_summary()
    summary["results"][0]["llm_calls"] = [{"request_id": "request-1"}]

    outcome = DirectRunOutcome.from_summary(
        summary,
        status=DirectRunStatus.SUCCEEDED,
    )

    assert outcome.status is DirectRunStatus.INCOMPLETE
    assert outcome.process_exit_code == 0
    assert outcome.trajectory_fidelity == "partial"


def test_legacy_task_status_is_preserved_by_outcome_coercion() -> None:
    summary = _runtime_exception_summary(status_key="task_status")
    summary["results"] = tuple(summary["results"])

    outcome = coerce_direct_run_outcome(summary)

    assert outcome.status is DirectRunStatus.INCOMPLETE
    assert outcome.process_exit_code == 0


def test_typed_legacy_outcome_is_reconciled_before_finalization() -> None:
    legacy_outcome = DirectRunOutcome(
        status=DirectRunStatus.SUCCEEDED,
        summary=_runtime_exception_summary(),
        process_exit_code=0,
        trajectory_fidelity="complete",
        llm_call_count=1,
        tool_call_count=0,
        action_count=1,
    )

    outcome = coerce_direct_run_outcome(legacy_outcome)

    assert outcome.status is DirectRunStatus.INCOMPLETE
    assert outcome.process_exit_code == 0
    assert outcome.trajectory_fidelity == "partial"
    assert outcome.llm_call_count == 1
    assert outcome.action_count == 1


def test_explicit_semantic_status_takes_precedence_over_legacy_task_status() -> None:
    outcome = DirectRunOutcome.from_summary(
        {
            "results": [
                {
                    "success": True,
                    "semantic_status": "succeeded",
                    "task_status": "incomplete",
                }
            ]
        },
        status=DirectRunStatus.SUCCEEDED,
    )

    assert outcome.status is DirectRunStatus.SUCCEEDED


def test_typed_reconciliation_counts_native_trajectory_as_partial_evidence() -> None:
    summary = _runtime_exception_summary()
    summary["results"][0]["trajectory"] = [
        {"action": {"role": "assistant", "content": None}}
    ]
    legacy_outcome = DirectRunOutcome(
        status=DirectRunStatus.SUCCEEDED,
        summary=summary,
        process_exit_code=0,
        trajectory_fidelity="complete",
        llm_call_count=0,
        tool_call_count=0,
        action_count=0,
    )

    outcome = coerce_direct_run_outcome(legacy_outcome)

    assert outcome.status is DirectRunStatus.INCOMPLETE
    assert outcome.trajectory_fidelity == "partial"
