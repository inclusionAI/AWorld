from __future__ import annotations

from aworld.self_evolve.trace_pack import TrajectoryLogRecord
from aworld.self_evolve.trajectory_context import (
    build_trajectory_context_snapshots,
    input_with_reconstructed_context,
)


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


def test_recorded_system_prompt_is_not_replayed_as_user_task_context() -> None:
    record = _record(
        "task",
        "session-a",
        "Run the current task",
        "Done",
        record_index=0,
    )
    step = dict(record.trajectory[0])
    state = dict(step["state"])
    state["messages"] = [
        {
            "role": "system",
            "content": "Old environment system prompt and tool catalog " * 2_000,
        },
        {"role": "user", "content": "Run the current task"},
    ]
    step["state"] = state
    record = TrajectoryLogRecord(
        record_index=record.record_index,
        task_id=record.task_id,
        record_metadata=record.record_metadata,
        trajectory=(step,),
    )

    snapshot = build_trajectory_context_snapshots((record,))[0]

    assert snapshot.prior_turns == ()
    assert input_with_reconstructed_context(
        {"content": "Run the current task"},
        snapshot,
    ) == {"content": "Run the current task"}


def test_recorded_conversation_context_is_bounded_and_keeps_recent_turns() -> None:
    record = _record(
        "task",
        "session-a",
        "Continue the current task",
        "Done",
        record_index=0,
    )
    step = dict(record.trajectory[0])
    state = dict(step["state"])
    state["messages"] = [
        {"role": "system", "content": "environment-only"},
        {"role": "user", "content": "old" * 4_000},
        {"role": "assistant", "content": "recent result"},
        {"role": "user", "content": "Continue the current task"},
    ]
    step["state"] = state
    record = TrajectoryLogRecord(
        record_index=record.record_index,
        task_id=record.task_id,
        record_metadata=record.record_metadata,
        trajectory=(step,),
    )

    snapshot = build_trajectory_context_snapshots(
        (record,),
        max_text_chars=128,
    )[0]
    reconstructed = input_with_reconstructed_context(
        {"content": "Continue the current task"},
        snapshot,
    )

    assert [turn.role for turn in snapshot.prior_turns] == ["user", "assistant"]
    assert sum(len(turn.content) for turn in snapshot.prior_turns) <= 128
    assert snapshot.prior_turns[-1].content == "recent result"
    assert "environment-only" not in reconstructed["content"]
