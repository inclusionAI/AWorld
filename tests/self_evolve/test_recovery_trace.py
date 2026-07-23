from __future__ import annotations

import json
from types import SimpleNamespace

from aworld.self_evolve.lessons import extract_lesson_records
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
)
from aworld.self_evolve.recovery_trace import (
    CONSTRAINT_RECOVERY_TRACE_SCHEMA_VERSION,
    RECOVERY_TRACE_SCHEMA_VERSION,
    replay_recovery_trace,
    trace_pack_recovery_summary,
    update_constraint_recovery_trace,
    validate_public_constraint_recovery_trace,
    validate_public_recovery_trace,
)
from aworld.self_evolve.replay import ReplayVariantResult
from aworld.self_evolve.trace_pack import build_trace_pack


def _step(status: str, tool: str) -> dict[str, object]:
    return {
        "action": {
            "tool_calls": [
                {"function": {"name": tool, "arguments": {"secret": "payload"}}}
            ]
        },
        "reward": {"status": status, "response": "private response payload"},
    }


def _failed_result(*, stdout_path: str | None = None) -> ReplayVariantResult:
    return ReplayVariantResult(
        variant_id="candidate",
        status="failed",
        trajectory=[],
        stdout_path=stdout_path,
        failure={"type": "TimeoutExpired", "reason": "replay timed out"},
    )


def _successful_result() -> ReplayVariantResult:
    return ReplayVariantResult(
        variant_id="candidate",
        status="succeeded",
        trajectory=[_step("ok", "artifact_reader")],
    )


def _aggregate_result(
    *,
    status: str,
    repetitions: tuple[ReplayVariantResult, ...],
) -> ReplayVariantResult:
    return ReplayVariantResult(
        variant_id="candidate",
        status=status,
        trajectory=(repetitions[-1].trajectory if status == "succeeded" else []),
        failure=(
            None
            if status == "succeeded"
            else {"reason": "one or more replay repetitions failed"}
        ),
        repetition_results=repetitions,
    )


def test_historical_failure_to_success_is_recovery_not_failure_memory() -> None:
    pack = build_trace_pack(
        [_step("failed", "fetch"), _step("succeeded", "artifact_reader")],
        source_kind="trajectory_log",
        task_id="private-task-id",
    )

    summary = trace_pack_recovery_summary(pack)
    lessons = extract_lesson_records((), target_scope={}, trace_packs=(pack,))

    assert summary["recovered"] is True
    assert summary["failure_count"] == 1
    assert summary["steps_to_recovery"] == 1
    assert [lesson.lesson_type for lesson in lessons] == [
        "trajectory_recovery_memory"
    ]
    assert lessons[0].metrics["recovery_trace"]["recovered"] is True


def test_replay_recovery_trace_handles_multiple_members_and_repetitions(
    tmp_path,
) -> None:
    timeout_stdout = tmp_path / "stdout.txt"
    timeout_stdout.write_text(
        "\n".join(
            (
                "No history file. Start chatting to generate history.",
                "   ▶ fetch",
                "   ▶ fetch",
                "   ▶ fetch",
                "   ▶ fetch",
                "   ▶ artifact_reader",
            )
        ),
        encoding="utf-8",
    )
    failed = _failed_result(stdout_path=str(timeout_stdout))
    succeeded = _successful_result()
    baseline_failed = _aggregate_result(
        status="failed",
        repetitions=(_failed_result(), _failed_result()),
    )
    partially_recovered = _aggregate_result(
        status="succeeded",
        repetitions=(succeeded, failed, succeeded),
    )
    unrecovered = _aggregate_result(
        status="failed",
        repetitions=(failed, failed, failed),
    )
    members = (
        SimpleNamespace(
            case_id="private-case-a",
            baseline=baseline_failed,
            candidate=partially_recovered,
        ),
        SimpleNamespace(
            case_id="private-case-b",
            baseline=baseline_failed,
            candidate=unrecovered,
        ),
    )

    trace = replay_recovery_trace(members)

    assert trace is not None
    assert trace["schema_version"] == RECOVERY_TRACE_SCHEMA_VERSION
    assert trace["member_count"] == 2
    assert trace["recovered_member_count"] == 1
    assert trace["partial_recovery_member_count"] == 1
    assert trace["candidate_success_rate"] == 0.333333
    assert trace["members"][0]["classification"] == "partial_recovery"
    assert trace["members"][0]["failed_progress_exceeded_success"] is True
    assert trace["members"][0]["failure_loop_detected"] is True
    assert "bound_post_checkpoint_exploration" in trace["guidance"]
    assert (
        "replace_repeated_failure_loop_with_bounded_strategy_switch"
        in trace["guidance"]
    )
    serialized = json.dumps(trace)
    assert "private-case" not in serialized
    assert "payload" not in serialized


def test_public_recovery_projection_drops_unknown_payload_fields() -> None:
    projected = validate_public_recovery_trace(
        {
            "schema_version": RECOVERY_TRACE_SCHEMA_VERSION,
            "member_count": 1,
            "candidate_intervention_required": True,
            "candidate_intervention_observed": False,
            "raw_response": "SECRET",
            "members": [
                {
                    "member_identity": "sha256:" + "a" * 64,
                    "classification": "unrecovered",
                    "failed_progress_max": 7,
                    "raw_arguments": {"token": "SECRET"},
                }
            ],
        }
    )

    assert projected is not None
    assert projected["member_count"] == 1
    assert projected["candidate_intervention_required"] is True
    assert projected["candidate_intervention_observed"] is False
    assert "SECRET" not in json.dumps(projected)


def test_blocked_replay_is_not_counted_as_failed_recovery_attempt() -> None:
    blocker = ReplayFailureEvent(
        code="replay_service_unavailable",
        owner=FailureOwner.INFRASTRUCTURE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.SHARED_RUN,
        repairable=False,
        summary="shared replay service unavailable",
    )
    blocked = ReplayVariantResult(
        variant_id="candidate",
        status="blocked",
        trajectory=[],
        blocked_by=(blocker,),
    )

    trace = replay_recovery_trace(
        (
            SimpleNamespace(
                case_id="case",
                baseline=blocked,
                candidate=blocked,
            ),
        )
    )

    assert trace is None


def test_constraint_recovery_trace_tracks_repeat_recovery_and_regression() -> None:
    first_id = "sha256:" + "a" * 64
    second_id = "sha256:" + "b" * 64

    trace = update_constraint_recovery_trace(
        None,
        violated_constraint_ids=(first_id,),
        contract_constraint_ids=(first_id,),
    )
    trace = update_constraint_recovery_trace(
        trace,
        violated_constraint_ids=(first_id,),
        contract_constraint_ids=(first_id, second_id),
    )
    assert trace is not None
    assert trace["repeated_violation_count"] == 1
    assert (
        "switch_implementation_for_repeated_constraint_failure"
        in trace["guidance"]
    )

    trace = update_constraint_recovery_trace(
        trace,
        violated_constraint_ids=(second_id,),
        contract_constraint_ids=(first_id, second_id),
    )
    assert trace is not None
    assert trace["recovered_constraint_count"] == 1

    trace = update_constraint_recovery_trace(
        trace,
        violated_constraint_ids=(first_id,),
        contract_constraint_ids=(first_id, second_id),
    )
    assert trace is not None
    assert trace["regressed_constraint_count"] == 1
    first = next(
        item
        for item in trace["constraints"]
        if item["constraint_identity"] == first_id
    )
    assert first["status"] == "regressed"


def test_public_constraint_recovery_trace_is_identity_only() -> None:
    projected = validate_public_constraint_recovery_trace(
        {
            "schema_version": CONSTRAINT_RECOVERY_TRACE_SCHEMA_VERSION,
            "attempt_count": 2,
            "raw_constraint": "SECRET",
            "constraints": [
                {
                    "constraint_identity": "sha256:" + "c" * 64,
                    "status": "active",
                    "violation_attempt_count": 2,
                    "payload": "SECRET",
                }
            ],
        }
    )

    assert projected is not None
    assert projected["attempt_count"] == 2
    assert "SECRET" not in json.dumps(projected)
