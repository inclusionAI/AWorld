from __future__ import annotations

from aworld.self_evolve.trace_pack import TrajectoryLogRecord
from aworld.self_evolve.trajectory_context import build_trajectory_context_snapshots


def _record(
    task_id: str,
    session_id: str,
    task: str,
    answer: str,
    *,
    record_index: int,
    parent_task_id: str | None = None,
) -> TrajectoryLogRecord:
    metadata = {"task_id": task_id}
    if parent_task_id is not None:
        metadata["parent_task_id"] = parent_task_id
    return TrajectoryLogRecord(
        record_index=record_index,
        task_id=task_id,
        record_metadata=metadata,
        trajectory=(
            {
                "meta": {
                    "step": 1,
                    "task_id": task_id,
                    "session_id": session_id,
                    "agent_id": "agent",
                    "pre_agent": "runner",
                },
                "state": {
                    "input": {"content": task},
                    "messages": [{"role": "user", "content": task}],
                },
                "action": {
                    "content": answer,
                    "tool_calls": [],
                    "is_agent_finished": True,
                },
                "reward": {"status": "ok"},
            },
        ),
    )


def test_context_prefers_explicit_parent() -> None:
    records = (
        _record("first", "session-a", "Start", "Finished", record_index=0),
        _record(
            "next",
            "session-b",
            "Continue the current task",
            "Done",
            record_index=1,
            parent_task_id="first",
        ),
    )

    snapshot = build_trajectory_context_snapshots(records)[1]

    assert snapshot.link_strategy == "explicit_parent"
    assert snapshot.prior_turns[-1].content == "Finished"
    assert snapshot.prior_turns[-1].source_task_id == "first"


def test_context_prefers_same_session_predecessor_for_continuation() -> None:
    records = (
        _record("first", "session-a", "Start", "Finished", record_index=0),
        _record(
            "next",
            "session-a",
            "Continue the current task",
            "Done",
            record_index=1,
        ),
    )

    snapshot = build_trajectory_context_snapshots(records)[1]

    assert snapshot.link_strategy == "same_session_predecessor"
    assert snapshot.prior_turns[-1].content == "Finished"


def test_context_uses_adjacent_fallback_only_for_explicit_continuation() -> None:
    records = (
        _record("first", "session-a", "Start", "Finished", record_index=0),
        _record(
            "next",
            "session-b",
            "Continue the current task with additional operator steering",
            "Done",
            record_index=1,
        ),
    )

    snapshot = build_trajectory_context_snapshots(records)[1]

    assert snapshot.link_strategy == "adjacent_record_fallback"
    assert snapshot.prior_turns[0].content == "Start"


def test_unrelated_adjacent_record_is_not_joined() -> None:
    records = (
        _record("first", "session-a", "Start", "Finished", record_index=0),
        _record(
            "next",
            "session-b",
            "New independent task",
            "Done",
            record_index=1,
        ),
    )

    snapshot = build_trajectory_context_snapshots(records)[1]

    assert snapshot.prior_turns == ()
    assert snapshot.link_strategy is None


def test_context_snapshot_keeps_full_step_count_and_stable_fingerprint() -> None:
    record = _record("first", "session-a", "Start", "Finished", record_index=0)

    first = build_trajectory_context_snapshots((record,))[0]
    second = build_trajectory_context_snapshots((record,))[0]

    assert first.step_count == 1
    assert len(first.steps) == 1
    assert first.fingerprint == second.fingerprint
