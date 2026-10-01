from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.execution_protocol import (
    ControllerAction,
    ExecutionProtocolPolicy,
    ProtocolMode,
)
from aworld.runners.execution_protocol import (
    configure_execution_protocol,
    consume_execution_protocol_guidance,
    load_execution_protocol_state,
)
from aworld.runners.post_tool_progress import record_semantic_tool_progress


def _record_failure(context: Context, index: int):
    return record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[
            ActionModel(
                tool_name="terminal",
                action_name="execute",
                tool_call_id=f"call-{index}",
                params={"command": f"candidate-{index} --retry"},
            )
        ],
        observation=Observation(
            action_result=[
                ActionResult(
                    success=False,
                    error=f"AssertionError: row {index} at /tmp/run-{index}/check.py",
                )
            ]
        ),
    )


def test_repeated_failure_signature_offers_bounded_checkpoint():
    context = Context(task_id="semantic-ledger")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    baseline = _record_failure(context, 0)
    assert baseline["no_goal_progress_count"] == 0

    for index in range(1, 7):
        state = _record_failure(context, index)

    assert state["failure_signature"] == baseline["failure_signature"]
    assert state["no_goal_progress_count"] == 6
    metrics = context.context_info["execution_protocol_metrics"]
    assert metrics["last_action"] == ControllerAction.REQUEST_REPLAN.value


def test_changed_failure_signature_is_meaningful_progress():
    context = Context(task_id="semantic-failure-change")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    _record_failure(context, 1)
    repeated = _record_failure(context, 2)
    assert repeated["no_goal_progress_count"] == 1

    changed = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(success=False, error="PermissionError: denied")]
        ),
    )

    assert changed["failure_signature"] != repeated["failure_signature"]
    assert changed["goal_progress"] is True
    assert changed["no_goal_progress_count"] == 0
    assert changed["last_meaningful_progress_at"] is not None


def test_alternating_known_failure_signatures_do_not_reset_progress():
    context = Context(task_id="semantic-failure-abab")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    states = []
    for index, error in enumerate(
        (
            "AssertionError: alpha",
            "PermissionError: beta",
            "AssertionError: alpha",
            "PermissionError: beta",
            "AssertionError: alpha",
            "PermissionError: beta",
        )
    ):
        states.append(
            record_semantic_tool_progress(
                context,
                tool_name="terminal",
                agent_id="agent",
                actions=[
                    ActionModel(
                        tool_name="terminal",
                        action_name="execute",
                        params={"command": f"attempt-{index}"},
                    )
                ],
                observation=Observation(
                    action_result=[ActionResult(success=False, error=error)]
                ),
            )
        )

    assert states[0]["goal_progress"] is True
    assert states[1]["goal_progress"] is True
    assert all(state["goal_progress"] is False for state in states[2:])
    assert states[-1]["no_goal_progress_count"] == 4
    assert len(states[-1]["recent_failure_signatures"]) == 6


def test_semantic_progress_ledger_env_opt_out(monkeypatch):
    monkeypatch.setenv("AWORLD_SEMANTIC_PROGRESS_LEDGER", "false")
    context = Context(task_id="semantic-ledger-off")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    state = _record_failure(context, 1)

    assert state["semantic_progress_enabled"] is False
    assert state["goal_progress_observable"] is False
    assert state["no_goal_progress_count"] == 0


def test_two_ineffective_replans_stop_injecting_without_finalizing():
    context = Context(task_id="semantic-replan-limit")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
            max_replans=2,
        ),
    )
    _record_failure(context, 0)
    next_index = 1
    for _ in range(2):
        for _ in range(6):
            _record_failure(context, next_index)
            next_index += 1
        guidance = consume_execution_protocol_guidance(context, "agent")
        assert guidance is not None and "checkpoint" in guidance

    for _ in range(6):
        _record_failure(context, next_index)
        next_index += 1

    metrics = context.context_info["execution_protocol_metrics"]
    assert metrics["last_action"] == ControllerAction.CONTINUE.value
    assert metrics["last_reason"] == "replan_limit_reached"
    assert consume_execution_protocol_guidance(context, "agent") is None
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.replan_count == 2
    assert protocol_state.finalization_entered is False


def test_successful_tools_without_progress_channel_remain_unknown():
    context = Context(task_id="semantic-progress-unknown")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
            no_goal_progress_threshold=2,
            stagnation_event_threshold=2,
        ),
    )

    for index in range(12):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="execute",
                    tool_call_id=f"call-{index}",
                    params={"command": f"inspect-stage-{index}"},
                )
            ],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"novel observation {index}", success=True)
                ]
            ),
        )

    assert state["goal_progress_observable"] is None
    assert state["goal_progress"] is False
    assert state["no_goal_progress_count"] == 0
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.replan_count == 0
    assert protocol_state.finalization_entered is False


def test_repeated_failures_remain_observable_without_artifact_channel():
    context = Context(task_id="semantic-failure-observable")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    first = _record_failure(context, 0)
    repeated = _record_failure(context, 1)

    assert first["goal_progress_observable"] is True
    assert repeated["goal_progress_observable"] is True
    assert repeated["no_goal_progress_count"] == 1
