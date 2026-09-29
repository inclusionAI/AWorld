from dataclasses import FrozenInstanceError, replace

import pytest

from aworld.core.execution_protocol import (
    EventKind,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolState,
    ProtocolMode,
    ProtocolScope,
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("history_limit", 0),
        ("history_limit", 257),
        ("activation_event_threshold", 0),
        ("stagnation_event_threshold", True),
        ("max_replans", -1),
        ("max_replans", 17),
        ("max_final_reviews", 2),
        ("max_repairs", 2),
        ("finalization_reserve_seconds", -0.1),
        ("final_review_timeout_seconds", 0),
        ("final_review_timeout_seconds", 3601),
    ],
)
def test_policy_rejects_invalid_bounds(field, value):
    with pytest.raises(ValueError):
        ExecutionProtocolPolicy(**{field: value})


def test_policy_coerces_valid_mode_and_is_immutable():
    policy = ExecutionProtocolPolicy(mode="guide")

    assert policy.mode is ProtocolMode.GUIDE
    with pytest.raises(FrozenInstanceError):
        policy.mode = ProtocolMode.OFF


def test_policy_has_a_stable_serialized_round_trip():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        history_limit=9,
        activation_event_threshold=15,
        max_replans=1,
        finalization_reserve_seconds=42.5,
        final_review_timeout_seconds=30,
    )

    assert ExecutionProtocolPolicy.from_dict(policy.to_dict()) == policy
    with pytest.raises(ValueError, match="schema"):
        ExecutionProtocolPolicy.from_dict(
            {**policy.to_dict(), "schema_version": "future"}
        )


def test_policy_restores_pre_activation_v1_with_safe_default():
    payload = ExecutionProtocolPolicy(mode="guide").to_dict()
    payload.pop("activation_event_threshold")
    payload.pop("final_review_timeout_seconds")

    restored = ExecutionProtocolPolicy.from_dict(payload)

    assert restored.activation_event_threshold == 6
    assert restored.final_review_timeout_seconds == 45.0


def test_event_validates_generic_measurements_and_review_shape():
    with pytest.raises(ValueError):
        ExecutionProtocolEvent(kind=EventKind.TOOL_OBSERVATION, current_step=-1)
    with pytest.raises(ValueError):
        ExecutionProtocolEvent(kind=EventKind.REVIEW_RESULT)
    with pytest.raises(ValueError):
        ExecutionProtocolEvent(
            kind=EventKind.TOOL_OBSERVATION,
            review_outcome="unknown",
        )


def test_state_round_trip_contains_no_raw_task_or_tool_text():
    scope = ProtocolScope(task_id="task", task_epoch=3, agent_id="agent")
    event = ExecutionProtocolEvent(
        kind=EventKind.TOOL_OBSERVATION,
        operation_hash="operation-hash",
        result_hash="result-hash",
        current_step=7,
        remaining_seconds=91.5,
    )
    state = replace(
        ExecutionProtocolState.initial(scope).append_event(event, history_limit=4),
        long_horizon_armed=True,
    )

    restored = ExecutionProtocolState.from_dict(state.to_dict())

    assert restored == state
    assert restored.long_horizon_armed is True
    payload = str(state.to_dict())
    assert "operation-hash" in payload
    assert "task_input" not in payload
    assert "tool_output" not in payload


def test_state_rejects_corrupt_persisted_history():
    scope = ProtocolScope(task_id="task", task_epoch=3, agent_id="agent")
    state = ExecutionProtocolState.initial(scope).append_event(
        ExecutionProtocolEvent(kind=EventKind.TOOL_OBSERVATION), history_limit=4
    )
    payload = state.to_dict()
    payload["history"][0]["goal_progress"] = "claimed"

    with pytest.raises(ValueError, match="goal_progress"):
        ExecutionProtocolState.from_dict(payload)
