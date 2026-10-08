from types import SimpleNamespace

import pytest

from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.event.base import Constants, TopicType
from aworld.core.task import Task
from aworld.core.tool.base import ensure_action_results
from aworld.runners.event_runner import TaskEventRunner
from aworld.runners.post_tool_progress import arm_post_tool_progress_watchdog


def _build_runner() -> TaskEventRunner:
    runner = TaskEventRunner.__new__(TaskEventRunner)
    runner.task = Task(
        id="task-1",
        session_id="session-1",
        conf={
            "post_tool_progress_watchdog_timeout_seconds": 5,
        },
    )
    runner.context = Context(task_id="task-1")
    runner.context.set_task(runner.task)
    runner.context.session = SimpleNamespace(session_id="session-1")
    runner.event_mng = SimpleNamespace(emit_message=None)
    runner._task_response = None
    return runner


@pytest.mark.asyncio
async def test_post_tool_progress_watchdog_retries_twice_then_returns_scoreable_stop(monkeypatch):
    runner = _build_runner()
    emitted = []

    async def capture(message):
        emitted.append(message)
        return True

    runner.event_mng.emit_message = capture
    runner.context.context_info["post_tool_progress_watchdog"] = {
        "agent_id": "agent-1",
        "tool_name": "terminal",
        "followup_sender": "terminal",
        "tool_call_ids": ["call-1"],
        "armed_at": 10.0,
        "retry_count": 0,
        "followup_observation": {
            "content": "tool finished",
            "observer": "terminal",
            "from_agent_name": "agent-1",
            "action_result": [
                {
                    "tool_call_id": "call-1",
                    "tool_name": "terminal",
                    "content": "ok",
                    "success": True,
                }
            ],
        },
    }

    monkeypatch.setattr("aworld.runners.event_runner.time.time", lambda: 20.0)
    handled = await runner._check_post_tool_progress_watchdog()

    assert handled is True
    assert emitted[0].category == Constants.AGENT
    assert emitted[0].receiver == "agent-1"
    assert emitted[0].headers["history_sanitized_retry"] is True
    retry_turn = emitted[0].headers["context"].record_model_turn(
        "watchdog-retry-request", []
    )
    assert retry_turn.cause.value == "framework_retry"
    assert runner.context.context_info["post_tool_progress_watchdog"]["retry_count"] == 1
    assert runner.context.context_info["post_tool_progress_metrics"]["watchdog_trigger_count"] == 1
    assert runner.context.context_info["post_tool_progress_metrics"]["sanitized_history_retry_count"] == 1

    runner.context.context_info["post_tool_progress_watchdog"]["armed_at"] = 20.0
    monkeypatch.setattr("aworld.runners.event_runner.time.time", lambda: 30.0)
    handled = await runner._check_post_tool_progress_watchdog()

    assert handled is True
    assert emitted[1].category == Constants.AGENT
    assert emitted[1].headers["history_sanitized_retry"] is True
    assert "post_tool_continuation_token" not in emitted[1].headers
    assert runner.context.context_info["post_tool_progress_watchdog"]["retry_count"] == 2

    runner.context.context_info["post_tool_progress_watchdog"]["armed_at"] = 30.0
    monkeypatch.setattr("aworld.runners.event_runner.time.time", lambda: 40.0)
    handled = await runner._check_post_tool_progress_watchdog()

    assert handled is True
    assert emitted[2].category == Constants.TASK
    assert emitted[2].topic == TopicType.ERROR
    assert emitted[2].headers["task_failure"]["origin"] == "task"
    assert emitted[2].headers["task_failure"]["code"] == "post_tool_continuation_lost"
    assert "post-tool progress watchdog" in emitted[2].payload.msg
    assert "post_tool_progress_watchdog" not in runner.context.context_info


@pytest.mark.asyncio
async def test_post_tool_progress_watchdog_accepts_null_action_error(monkeypatch):
    runner = _build_runner()
    emitted = []

    async def capture(message):
        emitted.append(message)
        return True

    runner.event_mng.emit_message = capture
    observation = Observation(
        content="tool finished",
        observer="developer",
        from_agent_name="agent-1",
    )
    ensure_action_results(
        observation,
        [ActionModel(tool_name="developer")],
        success=False,
        default_content="ok",
    )
    arm_post_tool_progress_watchdog(
        runner.context,
        tool_name="developer",
        agent_id="agent-1",
        actions=[ActionModel(tool_name="developer")],
        followup_observation=observation,
        followup_sender="developer",
    )
    runner.context.context_info["post_tool_progress_watchdog"]["armed_at"] = 10.0

    monkeypatch.setattr("aworld.runners.event_runner.time.time", lambda: 20.0)
    handled = await runner._check_post_tool_progress_watchdog()

    assert handled is True
    assert len(emitted) == 1
    assert emitted[0].category == Constants.AGENT
    assert emitted[0].receiver == "agent-1"
    assert emitted[0].payload.action_result[0].error is None
    assert emitted[0].payload.action_result[0].tool_name is None
    assert emitted[0].payload.action_result[0].action_name is None
    assert emitted[0].payload.action_result[0].tool_call_id is None


def test_repeated_operations_do_not_inject_instructions_into_tool_results():
    context = Context(task_id="progress-guard-abab")

    def arm(label: str, index: int) -> Observation:
        command = (
            "sed -n '214,330p' ars.R"
            if label == "a"
            else "sed -n '331,420p' ars.R"
        )
        result_text = f"private-tool-result-{label}"
        observation = Observation(
            content=result_text,
            action_result=[
                ActionResult(content=result_text, success=True)
            ],
        )
        arm_post_tool_progress_watchdog(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    tool_call_id=f"call-{index}",
                    params={"code": command},
                )
            ],
            followup_observation=observation,
            followup_sender="terminal",
        )
        return observation

    for index, label in enumerate(("a", "b", "a", "b", "a"), start=1):
        observation = arm(label, index)

    assert observation.content == "private-tool-result-a"
    assert observation.action_result[0].content == "private-tool-result-a"
    assert "progress_guard" not in (observation.info or {})
    state = context.context_info["post_tool_progress_watchdog"]
    assert state["followup_observation"]["content"] == "private-tool-result-a"
    from aworld.runners.post_tool_progress import semantic_progress_for_agent
    assert semantic_progress_for_agent(context, agent_id="agent")["repetition_count"] == 3


def test_sandbox_receipts_track_consecutive_reads_until_real_mutation():
    from aworld.runners.post_tool_progress import semantic_progress_for_agent

    context = Context(task_id="mutation-gate-progress")
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="call-1",
        params={"code": "cat README.md"},
    )

    def observe(
        effect: str,
        *,
        workspace_mutated=False,
        known_mutation_executed=False,
        index: int,
    ):
        terminal_receipt = (
            {
                "effect": "mutating",
                "executed": True,
                "exit_code": 0,
                "timed_out": False,
            }
            if known_mutation_executed
            else None
        )
        observation = Observation(
            content=f"result-{index}",
            action_result=[
                ActionResult(
                    content=f"result-{index}",
                    success=effect != "blocked_read_only",
                    metadata={
                        "sandbox_observation": {
                            "effect": effect,
                            "workspace_mutated": workspace_mutated,
                            "workspace_generation": int(bool(workspace_mutated)),
                            "terminal_execution_receipt": terminal_receipt,
                        }
                    },
                )
            ],
        )
        arm_post_tool_progress_watchdog(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[action],
            followup_observation=observation,
            followup_sender="terminal",
        )

    observe("read_only", index=1)
    observe("read_only", index=2)
    state = semantic_progress_for_agent(context, agent_id="agent")
    assert state["consecutive_read_only_observations"] == 2
    assert state["workspace_mutation_observed"] is False

    observe("blocked_read_only", index=3)
    assert semantic_progress_for_agent(
        context, agent_id="agent"
    )["consecutive_read_only_observations"] == 2

    observe(
        "mutating",
        workspace_mutated=None,
        known_mutation_executed=True,
        index=4,
    )
    state = semantic_progress_for_agent(context, agent_id="agent")
    assert state["known_mutation_executed"] is True
    assert state["workspace_mutated"] is False
    assert state["workspace_mutation_observed"] is False
    assert state["goal_progress"] is False

    observe("mutating", workspace_mutated=True, index=5)
    state = semantic_progress_for_agent(context, agent_id="agent")
    assert state["consecutive_read_only_observations"] == 0
    assert state["workspace_mutation_observed"] is True


@pytest.mark.asyncio
async def test_successful_mutation_receipt_opens_one_real_hook_validation_window():
    from aworld.core.event.base import Message
    from aworld.core.execution_protocol import ExecutionProtocolPolicy, ProtocolMode
    from aworld.runners.execution_protocol import (
        build_execution_protocol_telemetry,
        configure_execution_protocol,
        record_model_execution_profile,
    )
    from aworld.runners.hook.agent_hooks import MutationGatePreToolHook
    from aworld.runners.post_tool_progress import semantic_progress_for_agent
    from aworld.sandbox.terminal_receipt import (
        build_terminal_execution_receipt,
        plan_terminal_execution,
    )

    context = Context(task_id="receipt-validation-window")
    context.set_task(
        Task(id="receipt-validation-window", input="create output", timeout=600)
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    assert record_model_execution_profile(
        context,
        "agent",
        {
            "horizon": "long",
            "confidence": 0.9,
            "milestone_count": 2,
            "expected_tool_actions": 10,
            "verification_required": True,
            "workspace_mutation_required": True,
        },
    ) is not None

    read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="validation-read",
        agent_name="agent",
        params={"code": "cat candidate.txt"},
    )

    def observe(
        action: ActionModel,
        *,
        effect: str,
        index: int,
        terminal_receipt=None,
    ) -> None:
        observation = Observation(
            content=f"receipt-{index}",
            action_result=[
                ActionResult(
                    content=f"receipt-{index}",
                    success=True,
                    metadata={
                        "sandbox_observation": {
                            "effect": effect,
                            "workspace_mutated": None,
                            "workspace_generation": index,
                            "terminal_execution_receipt": terminal_receipt,
                        }
                    },
                )
            ],
        )
        arm_post_tool_progress_watchdog(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[action],
            followup_observation=observation,
            followup_sender="terminal",
        )

    for index in range(1, 9):
        observe(read, effect="read_only", index=index)

    hook = MutationGatePreToolHook()
    message = Message(
        category="tool_call", payload=[read], sender="agent"
    )
    assert await hook.exec(message, context) is not None

    repair_code = "touch candidate.txt"
    repair = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="known-repair",
        agent_name="agent",
        params={"code": repair_code},
    )
    receipt = build_terminal_execution_receipt(
        code=repair_code,
        plan=plan_terminal_execution(repair_code),
        executed=True,
        exit_code=0,
        timed_out=False,
        mutation_observed=None,
    )
    observe(repair, effect="mutating", index=9, terminal_receipt=receipt)

    semantic = semantic_progress_for_agent(context, agent_id="agent")
    assert semantic["known_mutation_executed"] is True
    assert semantic["workspace_mutation_observed"] is False
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["mutation_gate_active"] is False
    assert telemetry["mutation_gate_validation_window_open"] is True
    assert await hook.exec(message, context) is None

    # The permitted validation produces a normal read receipt.  Because no
    # actual candidate mutation was observed, the pre-candidate latch closes
    # again instead of treating a potential write as goal progress.
    observe(read, effect="read_only", index=10)
    assert await hook.exec(message, context) is not None
