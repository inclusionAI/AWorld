from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.task import TaskResponse
from aworld.models.model_response import ModelResponse, ToolCall
from aworld.output.base import ChunkOutput, ToolResultOutput
from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.steering.coordinator import SteeringCoordinator


class _FakeStreamingOutputs:
    def __init__(self, *, events=None, response=None):
        self._events = list(events or [])
        self._task_response = response
        self._visited_outputs = []
        self._run_impl_task = None
        self.is_complete = True

    async def stream_events(self):
        for event in self._events:
            yield event

    def response(self):
        return self._task_response


class _ExplodingStreamingOutputs:
    def __init__(self, exc: Exception):
        self._exc = exc
        self._visited_outputs = []
        self._task_response = None
        self._run_impl_task = None
        self.is_complete = True

    async def stream_events(self):
        raise self._exc
        yield  # pragma: no cover

    def response(self):
        return self._task_response


def _build_executor(*, session_id: str = "sess-1"):
    agent = Agent(name="developer", conf=AgentConfig(skill_configs={}))
    output_buffer = StringIO()
    executor = LocalAgentExecutor(
        Swarm(agent),
        console=Console(file=output_buffer, force_terminal=False, width=100),
        session_id=session_id,
    )
    return executor, output_buffer


def _stub_chat_dependencies(
    executor: LocalAgentExecutor,
    monkeypatch: pytest.MonkeyPatch,
    outputs,
):
    task = SimpleNamespace(
        id="task-1",
        session_id="sess-1",
        context=SimpleNamespace(get_llm_calls=lambda: []),
    )

    monkeypatch.setattr(executor, "_update_session_last_used", lambda _session_id: None)
    monkeypatch.setattr(executor, "_build_task", AsyncMock(return_value=task))
    monkeypatch.setattr(executor, "_publish_hud_task_started", lambda _task: None)
    monkeypatch.setattr(executor, "_publish_hud_task_finished", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor, "_publish_hud_llm_observability", lambda *args, **kwargs: {})
    monkeypatch.setattr(executor, "_hud_is_active", lambda: False)
    monkeypatch.setattr(executor, "_run_plugin_task_hook", AsyncMock(return_value=[]))
    monkeypatch.setattr(executor, "_execute_hooks", AsyncMock(return_value=None))
    monkeypatch.setattr("aworld_cli.executors.local.Runners.streamed_run_task", lambda task: outputs)


@pytest.mark.asyncio
async def test_local_executor_active_steering_stream_error_skips_console_panel(
    monkeypatch: pytest.MonkeyPatch,
):
    executor, output_buffer = _build_executor()
    events: list[dict[str, str]] = []
    executor._active_steering_event_sink = events.append
    _stub_chat_dependencies(
        executor,
        monkeypatch,
        _ExplodingStreamingOutputs(RuntimeError("stream boom")),
    )

    with pytest.raises(RuntimeError, match="stream boom"):
        await executor.chat("hello")

    assert events[-1]["kind"] == "error"
    assert "stream boom" in events[-1]["text"]
    assert "Stream Error" not in output_buffer.getvalue()


@pytest.mark.asyncio
async def test_local_executor_active_steering_commits_final_answer_without_message_output(
    monkeypatch: pytest.MonkeyPatch,
):
    executor, output_buffer = _build_executor()
    events: list[dict[str, str]] = []
    executor._active_steering_event_sink = events.append
    _stub_chat_dependencies(
        executor,
        monkeypatch,
        _FakeStreamingOutputs(
            response=TaskResponse(success=True, answer="All done.", msg="ok"),
        ),
    )

    result = await executor.chat("hello")

    assert result == "All done."
    assert any(
        event["kind"] == "message_committed" and event["text"] == "All done."
        for event in events
    )
    assert "All done." not in output_buffer.getvalue()


@pytest.mark.asyncio
async def test_local_executor_active_steering_real_tool_result_path_uses_commit_buffer_summary(
    monkeypatch: pytest.MonkeyPatch,
):
    executor, _output_buffer = _build_executor()
    events: list[dict[str, str]] = []
    executor._active_steering_event_sink = events.append
    _stub_chat_dependencies(
        executor,
        monkeypatch,
        _FakeStreamingOutputs(
            events=[
                ToolResultOutput(
                    tool_name="terminal",
                    action_name="bash",
                    data="\n".join(f"line {index}" for index in range(1, 11)),
                    metadata={"exit_code": 7},
                )
            ],
            response=TaskResponse(success=True, answer="done", msg="ok"),
        ),
    )

    result = await executor.chat("hello")

    assert result == "done"
    tool_event = next(event for event in events if event["kind"] == "tool_result_committed")
    assert "Exit code: 7" in tool_event["text"]
    assert "... (" in tool_event["text"]


@pytest.mark.asyncio
async def test_local_executor_active_steering_yields_at_tool_checkpoint_when_pending_steering_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    executor, _output_buffer = _build_executor(session_id="sess-1")
    events: list[dict[str, str]] = []
    executor._active_steering_event_sink = events.append
    executor._base_runtime = SimpleNamespace(_steering=SteeringCoordinator())
    executor._base_runtime._steering.begin_task("sess-1", "task-1")
    executor._base_runtime._steering.enqueue_text("sess-1", "Focus on the architecture.")
    _stub_chat_dependencies(
        executor,
        monkeypatch,
        _FakeStreamingOutputs(
            events=[
                ToolResultOutput(
                    tool_name="terminal",
                    action_name="bash",
                    data="first line\nsecond line",
                    metadata={"exit_code": 0},
                )
            ],
            response=TaskResponse(success=True, answer="done", msg="ok"),
        ),
    )

    result = await executor.chat("hello")

    assert result == ""
    assert any(event["kind"] == "tool_result_committed" for event in events)
    assert any(
        call.args[0] == "steering_checkpoint"
        for call in executor._run_plugin_task_hook.await_args_list
    )
    assert not any(
        call.args[0] == "task_completed"
        for call in executor._run_plugin_task_hook.await_args_list
    )
    snapshot = executor._base_runtime._steering.snapshot("sess-1")
    assert snapshot["pending_count"] == 1


@pytest.mark.asyncio
async def test_local_executor_active_steering_yields_before_planned_tool_executes(
    monkeypatch: pytest.MonkeyPatch,
):
    executor, _output_buffer = _build_executor(session_id="sess-1")
    events: list[dict[str, str]] = []
    executor._active_steering_event_sink = events.append
    executor._base_runtime = SimpleNamespace(_steering=SteeringCoordinator())
    executor._base_runtime._steering.begin_task("sess-1", "task-1")
    executor._base_runtime._steering.enqueue_text("sess-1", "Focus on the architecture.")
    _stub_chat_dependencies(
        executor,
        monkeypatch,
        _FakeStreamingOutputs(
            events=[
                ChunkOutput(
                    data=ModelResponse(
                        id="resp-1",
                        model="test-model",
                        tool_calls=[
                            ToolCall.from_dict(
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "CAST_SEARCH__glob_search",
                                        "arguments": '{"pattern":"**/context*.py"}',
                                    },
                                }
                            )
                        ],
                    ),
                    metadata={},
                )
            ],
            response=TaskResponse(success=True, answer="done", msg="ok"),
        ),
    )

    result = await executor.chat("hello")

    assert result == ""
    assert any(
        call.args[0] == "steering_checkpoint"
        and call.args[1]["checkpoint"] == "before_tool_call"
        for call in executor._run_plugin_task_hook.await_args_list
    )
    assert any(
        event["kind"] == "status_changed" and "Applying queued steering" in event["text"]
        for event in events
    )
    assert not any(
        call.args[0] == "task_completed"
        for call in executor._run_plugin_task_hook.await_args_list
    )


@pytest.mark.asyncio
async def test_local_executor_steering_checkpoint_hook_can_defer_pause():
    executor = object.__new__(LocalAgentExecutor)
    executor.session_id = "sess-1"
    executor._active_steering_event_sink = lambda _event: None
    executor._base_runtime = SimpleNamespace(_steering=SteeringCoordinator())
    executor._base_runtime._steering.begin_task("sess-1", "task-1")
    executor._base_runtime._steering.enqueue_text("sess-1", "Focus on the architecture.")
    executor._run_plugin_task_hook = AsyncMock(
        return_value=[
            (
                None,
                SimpleNamespace(
                    action="deny",
                    system_message=None,
                ),
            )
        ]
    )
    executor._emit_active_steering_event = MagicMock()

    should_pause = await executor._should_pause_for_queued_steering_checkpoint(
        task_id="task-1",
        checkpoint="after_tool_result",
        current_tool="terminal",
        partial_answer="",
    )

    assert should_pause is False
    executor._run_plugin_task_hook.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_executor_steering_checkpoint_pauses_for_interrupt_without_text():
    executor = object.__new__(LocalAgentExecutor)
    executor.session_id = "sess-1"
    executor._active_steering_event_sink = lambda _event: None
    executor._base_runtime = SimpleNamespace(_steering=SteeringCoordinator())
    executor._base_runtime._steering.begin_task("sess-1", "task-1")
    assert executor._base_runtime._steering.request_interrupt("sess-1") is True
    executor._run_plugin_task_hook = AsyncMock(return_value=[])
    executor._emit_active_steering_status = MagicMock()

    should_pause = await executor._should_pause_for_queued_steering_checkpoint(
        task_id="task-1",
        checkpoint="after_tool_result",
        current_tool="terminal",
        partial_answer="partial",
    )

    assert should_pause is True
    executor._run_plugin_task_hook.assert_awaited_once()
    executor._emit_active_steering_status.assert_called_once_with("Applying queued steering")
