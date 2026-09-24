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
