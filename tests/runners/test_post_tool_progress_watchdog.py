from types import SimpleNamespace

import pytest

from aworld.core.context.base import Context
from aworld.core.event.base import Constants, TopicType
from aworld.core.task import Task
from aworld.runners.event_runner import TaskEventRunner


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
async def test_post_tool_progress_watchdog_retries_once_then_fails(monkeypatch):
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
    assert runner.context.context_info["post_tool_progress_watchdog"]["retry_count"] == 1
    assert runner.context.context_info["post_tool_progress_metrics"]["watchdog_trigger_count"] == 1
    assert runner.context.context_info["post_tool_progress_metrics"]["sanitized_history_retry_count"] == 1

    runner.context.context_info["post_tool_progress_watchdog"]["armed_at"] = 20.0
    monkeypatch.setattr("aworld.runners.event_runner.time.time", lambda: 30.0)
    handled = await runner._check_post_tool_progress_watchdog()

    assert handled is True
    assert emitted[1].category == Constants.TASK
    assert emitted[1].topic == TopicType.ERROR
    assert "post-tool progress watchdog" in emitted[1].payload.msg
    assert "post_tool_progress_watchdog" not in runner.context.context_info
