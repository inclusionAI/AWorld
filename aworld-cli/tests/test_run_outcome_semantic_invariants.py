from aworld_cli.run_outcome import (
    DirectRunOutcome,
    DirectRunStatus,
    coerce_direct_run_outcome,
)
import pytest


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
    assert outcome.completion_reason == "runtime_exception"
    assert outcome.to_dict()["completion_reason"] == "runtime_exception"


@pytest.mark.parametrize(
    "status",
    [
        DirectRunStatus.SUCCEEDED,
        DirectRunStatus.INCOMPLETE,
        DirectRunStatus.TASK_FAILED,
    ],
)
def test_outcome_preserves_bounded_skill_activation_evidence(
    status: DirectRunStatus,
) -> None:
    summary = {
        "results": [
            {
                "success": status is DirectRunStatus.SUCCEEDED,
                "skill_activation_evidence": [
                    {
                        "skill_name": "spreadsheet-recovery",
                        "canonical_skill_file": "/skills/spreadsheet-recovery/SKILL.md",
                        "canonical_skill_root": "/skills/spreadsheet-recovery",
                        "package_fingerprint": "sha256:" + "a" * 64,
                        "source": "aworld_cli_skill_activation_resolver",
                        "agent_name": "Aworld",
                    }
                ],
            }
        ]
    }

    outcome = DirectRunOutcome.from_summary(summary, status=status)

    assert outcome.to_dict()["skill_activation_evidence"] == [
        {
            "skill_name": "spreadsheet-recovery",
            "canonical_skill_file": "/skills/spreadsheet-recovery/SKILL.md",
            "canonical_skill_root": "/skills/spreadsheet-recovery",
            "package_fingerprint": "sha256:" + "a" * 64,
            "source": "aworld_cli_skill_activation_resolver",
            "agent_name": "Aworld",
        }
    ]


def test_outcome_rejects_unbounded_or_noncanonical_skill_activation_evidence() -> None:
    summary = {
        "skill_activation_evidence": [
            {
                "skill_name": "unsafe-skill",
                "canonical_skill_file": "/skills/unsafe-skill/SKILL.md",
                "canonical_skill_root": "/skills/unsafe-skill",
                "package_fingerprint": "not-a-fingerprint",
                "source": "aworld_cli_skill_activation_resolver",
                "agent_name": "Aworld",
                "usage": "must never enter control-plane evidence",
            }
        ]
    }

    outcome = DirectRunOutcome.from_summary(
        summary,
        status=DirectRunStatus.TASK_FAILED,
    )

    assert "skill_activation_evidence" not in outcome.to_dict()


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
