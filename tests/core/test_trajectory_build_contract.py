import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone

import pytest

from aworld.core.task import TaskResponse
from aworld.core.trajectory import (
    TrajectoryDeliveryReceipt,
    TrajectoryDeliveryState,
    TrajectoryDeliveryTargetReceipt,
    TrajectoryBuildResult,
    TrajectoryBuildStatus,
    TrajectoryFidelity,
    TrajectoryReasonCode,
    TrajectorySourceKind,
    canonical_trajectory_bytes,
    compute_trajectory_checksum,
)
from aworld.dataset.types import TrajectoryItem


CREATED_AT = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)


def _checksum(payload: list[dict] | None = None) -> str:
    return compute_trajectory_checksum(payload or [{"id": "step-1"}])


def _build_result(**overrides) -> TrajectoryBuildResult:
    values = {
        "task_id": "task-1",
        "session_id": "session-1",
        "trace_id": "trace-1",
        "task_epoch": 0,
        "status": TrajectoryBuildStatus.COMPLETE,
        "fidelity": TrajectoryFidelity.COMPLETE,
        "reason_code": None,
        "source_kind": TrajectorySourceKind.EVENT_STATE,
        "source_high_watermark": 2,
        "scheduled_updates": 2,
        "completed_updates": 2,
        "failed_updates": 0,
        "pending_updates": 0,
        "source_agent_messages": 1,
        "llm_call_count": 1,
        "tool_call_count": 0,
        "persisted_items": 1,
        "trajectory_ref": "artifact://trajectory/task-1",
        "source_checksum": _checksum([{"event": "agent-message"}]),
        "trajectory_checksum": _checksum(),
        "builder_version": "sar-v1",
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    return TrajectoryBuildResult(**values)


def test_trajectory_build_result_is_immutable_json_serializable_and_round_trips() -> None:
    result = _build_result()

    with pytest.raises(FrozenInstanceError):
        result.persisted_items = 2  # type: ignore[misc]

    payload = result.to_dict()
    assert payload["schema_version"] == "aworld.trajectory.build.v1"
    assert payload["status"] == "complete"
    assert payload["fidelity"] == "complete"
    assert payload["source_kind"] == "event_state"
    assert payload["created_at"] == "2026-08-31T03:00:00Z"
    assert "trajectory" not in payload
    assert "messages" not in payload
    assert json.loads(json.dumps(payload)) == payload
    assert TrajectoryBuildResult.from_dict(payload) == result


def test_canonical_trajectory_checksum_is_stable_across_mapping_order() -> None:
    first = [{"z": "中文", "nested": {"b": 2, "a": 1}}]
    second = [{"nested": {"a": 1, "b": 2}, "z": "中文"}]

    expected_bytes = '[{"nested":{"a":1,"b":2},"z":"中文"}]'.encode("utf-8")
    assert canonical_trajectory_bytes(first) == expected_bytes
    assert canonical_trajectory_bytes(second) == expected_bytes
    assert compute_trajectory_checksum(first) == compute_trajectory_checksum(second)
    assert compute_trajectory_checksum(first) == f"sha256:{hashlib.sha256(expected_bytes).hexdigest()}"


def test_canonical_trajectory_checksum_matches_existing_sar_model_projection() -> None:
    item = TrajectoryItem.model_validate(
        {
            "id": "step-1",
            "meta": {"session_id": "session-1", "task_id": "task-1"},
            "state": {"input": "question", "messages": [], "context": {}},
            "action": {"content": "answer", "tool_calls": [], "is_agent_finished": True},
            "reward": {"tool_outputs": [], "status": None, "score": None},
        }
    )

    assert compute_trajectory_checksum([item]) == compute_trajectory_checksum([item.to_dict()])


def test_canonical_trajectory_checksum_rejects_non_json_or_ambiguous_values() -> None:
    with pytest.raises(TypeError, match="string keys"):
        canonical_trajectory_bytes([{1: "ambiguous"}])
    with pytest.raises((TypeError, ValueError)):
        canonical_trajectory_bytes([{"unordered": {"a", "b"}}])
    with pytest.raises(ValueError):
        canonical_trajectory_bytes([{"not_finite": float("nan")}])


@pytest.mark.parametrize(
    "result",
    [
        _build_result(),
        _build_result(
            status=TrajectoryBuildStatus.PARTIAL,
            fidelity=TrajectoryFidelity.PARTIAL,
            reason_code=TrajectoryReasonCode.TRAJECTORY_UPDATE_TIMEOUT,
            scheduled_updates=3,
            completed_updates=2,
            failed_updates=0,
            pending_updates=1,
        ),
        _build_result(
            status=TrajectoryBuildStatus.EMPTY,
            fidelity=TrajectoryFidelity.UNAVAILABLE,
            reason_code=TrajectoryReasonCode.EXECUTION_NOT_STARTED,
            source_high_watermark=None,
            scheduled_updates=0,
            completed_updates=0,
            source_agent_messages=0,
            llm_call_count=0,
            persisted_items=0,
            trajectory_ref=None,
            source_checksum=None,
            trajectory_checksum=None,
        ),
        _build_result(
            status=TrajectoryBuildStatus.FAILED,
            fidelity=TrajectoryFidelity.BUILD_FAILED,
            reason_code=TrajectoryReasonCode.TRAJECTORY_BUILD_FAILED,
            scheduled_updates=1,
            completed_updates=0,
            failed_updates=1,
            source_agent_messages=1,
            persisted_items=0,
            trajectory_ref=None,
            trajectory_checksum=None,
        ),
    ],
)
def test_trajectory_build_outcomes_are_representable_without_semantic_content(
    result: TrajectoryBuildResult,
) -> None:
    assert result.task_id == "task-1"
    assert set(result.to_dict()).isdisjoint({"trajectory", "messages", "response", "tool_output"})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"pending_updates": -1}, "non-negative"),
        ({"scheduled_updates": 3}, "scheduled_updates"),
        (
            {
                "status": TrajectoryBuildStatus.COMPLETE,
                "fidelity": TrajectoryFidelity.COMPLETE,
                "scheduled_updates": 2,
                "completed_updates": 1,
                "pending_updates": 1,
            },
            "complete",
        ),
        (
            {
                "status": TrajectoryBuildStatus.EMPTY,
                "fidelity": TrajectoryFidelity.UNAVAILABLE,
                "reason_code": TrajectoryReasonCode.EXECUTION_NOT_STARTED,
            },
            "persisted_items",
        ),
        (
            {
                "status": TrajectoryBuildStatus.FAILED,
                "fidelity": TrajectoryFidelity.BUILD_FAILED,
                "reason_code": None,
                "scheduled_updates": 1,
                "completed_updates": 0,
                "failed_updates": 1,
                "persisted_items": 0,
                "trajectory_checksum": None,
            },
            "reason_code",
        ),
        ({"trajectory_checksum": "not-a-checksum"}, "trajectory_checksum"),
        ({"created_at": datetime(2026, 8, 31, 3, 0)}, "timezone-aware"),
    ],
)
def test_trajectory_build_result_rejects_inconsistent_states(overrides, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _build_result(**overrides)


def test_task_response_additively_binds_one_canonical_build_result() -> None:
    inline_trajectory = [{"id": "existing-step", "state": {}, "action": {}, "reward": {}}]
    result = _build_result()
    response = TaskResponse(
        id="task-1",
        success=True,
        answer="existing answer",
        trajectory=inline_trajectory,
        trajectory_build_result=result,
    )

    assert response.trajectory is inline_trajectory
    assert response.trajectory_build_result is result
    assert response.trajectory_status == "complete"
    assert response.trajectory_fidelity == "complete"
    assert response.trajectory_ref == "artifact://trajectory/task-1"
    assert response.trajectory_checksum == result.trajectory_checksum

    payload = response.to_dict()
    assert payload["trajectory"] == inline_trajectory
    assert payload["trajectory_build_result"] == result.to_dict()
    assert payload["trajectory_status"] == response.trajectory_status
    assert payload["trajectory_fidelity"] == response.trajectory_fidelity
    assert payload["trajectory_ref"] == response.trajectory_ref
    assert payload["trajectory_checksum"] == response.trajectory_checksum


def test_delivery_receipt_is_immutable_additive_and_contains_no_artifact_body_or_path() -> None:
    receipt = TrajectoryDeliveryReceipt(
        requested_format="dual",
        legacy=TrajectoryDeliveryTargetReceipt(status=TrajectoryDeliveryState.PERSISTED),
        v2=TrajectoryDeliveryTargetReceipt(
            status=TrajectoryDeliveryState.PERSISTED,
            record_checksum=_checksum(),
        ),
    )
    response = TaskResponse(trajectory_delivery_receipt=receipt)

    with pytest.raises(FrozenInstanceError):
        receipt.requested_format = "legacy"  # type: ignore[misc]

    payload = response.to_dict()["trajectory_delivery_receipt"]
    assert payload == receipt.to_dict()
    assert payload["legacy"]["status"] == "persisted"
    assert payload["v2"]["record_checksum"] == _checksum()
    serialized = json.dumps(payload)
    assert "trajectory.jsonl" not in serialized
    assert "artifact_path" not in serialized
    assert "trajectory" not in payload


def test_legacy_task_response_constructor_and_inline_trajectory_remain_compatible() -> None:
    inline_trajectory = [{"legacy": True}]
    response = TaskResponse(id="legacy-task", trajectory=inline_trajectory)

    assert response.trajectory == inline_trajectory
    assert response.trajectory_build_result is None
    assert response.trajectory_status is None
    assert response.trajectory_fidelity is None
    assert response.trajectory_ref is None
    assert response.trajectory_checksum is None
    assert response.to_dict()["trajectory"] == inline_trajectory


def test_compatibility_projections_cannot_drift_from_canonical_build_result() -> None:
    response = TaskResponse(trajectory_build_result=_build_result())

    with pytest.raises(AttributeError):
        response.trajectory_status = "failed"  # type: ignore[misc]

    response.trajectory_build_result = replace(
        response.trajectory_build_result,
        status=TrajectoryBuildStatus.PARTIAL,
        fidelity=TrajectoryFidelity.PARTIAL,
        reason_code=TrajectoryReasonCode.CHECKSUM_MISMATCH,
    )
    assert response.trajectory_status == "partial"
    assert response.trajectory_fidelity == "partial"
