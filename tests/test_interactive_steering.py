import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest
from prompt_toolkit.formatted_text import to_formatted_text

import aworld_cli.console as console_module
import aworld_cli.executors.file_parse_hook as file_parse_hook_module
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.event.base import Message
from aworld_cli.console import AWorldCLI, _ESC_INTERRUPT_SENTINEL
from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.steering.coordinator import SteeringCoordinator


class FakeRuntime:
    def __init__(self):
        self._steering = SteeringCoordinator()
        self.interrupt_requests: list[str] = []

    def request_session_interrupt(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        self.interrupt_requests.append(session_id)
        return self._steering.request_interrupt(session_id)


def test_active_steering_status_line_uses_runtime_override_when_present():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()
    cli._set_active_steering_status("Calling bash")

    text = cli._build_active_task_wait_text(time.monotonic() - 8.0)

    assert text.startswith("Working")
    assert "Calling bash" in text
    assert "type to steer" in text


def test_active_steering_status_line_keeps_working_animation_with_runtime_override(
    monkeypatch: pytest.MonkeyPatch,
):
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()
    cli._set_active_steering_status("Calling bash")
    monkeypatch.setattr(console_module.time, "monotonic", lambda: 100.0)

    text = cli._build_active_task_wait_text(98.6)

    assert "Working.." in text
    assert "Calling bash.." in text


def test_active_steering_status_line_deduplicates_working_override(
    monkeypatch: pytest.MonkeyPatch,
):
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()
    cli._set_active_steering_status("Working")
    monkeypatch.setattr(console_module.time, "monotonic", lambda: 100.0)

    text = cli._build_active_task_wait_text(98.6)

    assert "Working.. •" not in text
    assert text.count("Working..") == 1


@pytest.mark.asyncio
async def test_plain_text_is_queued_while_task_is_active():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    runtime._steering.begin_task("sess-1", "task-1")
    task = asyncio.create_task(asyncio.sleep(60))

    handled = await cli._handle_active_task_input(
        "Focus on the failing test first.",
        runtime=runtime,
        session_id="sess-1",
        executor_task=task,
    )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert handled is True
    snapshot = runtime._steering.snapshot("sess-1")
    assert snapshot["pending_count"] == 1
    assert snapshot["last_steer_excerpt"] == "Focus on the failing test first."


@pytest.mark.asyncio
async def test_plain_text_steering_queue_is_logged_to_workspace_session_log(tmp_path):
    cli = AWorldCLI()
    runtime = FakeRuntime()
    runtime._steering.begin_task("sess-1", "task-1")
    task = asyncio.create_task(asyncio.sleep(60))

    handled = await cli._handle_active_task_input(
        "Focus on the failing test first.",
        runtime=runtime,
        session_id="sess-1",
        executor_task=task,
        workspace_path=str(tmp_path),
        task_id="task-1",
    )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert handled is True

    log_path = tmp_path / ".aworld" / "memory" / "sessions" / "sess-1.jsonl"
    payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["event"] == "steering_queued"
    assert payload["session_id"] == "sess-1"
    assert payload["task_id"] == "task-1"
    assert payload["pending_count"] == 1
    assert payload["steering"]["text"] == "Focus on the failing test first."
    assert payload["steering"]["sequence"] == 1


@pytest.mark.asyncio
async def test_plain_text_steering_ack_is_committed_into_active_history():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()
    runtime = FakeRuntime()
    runtime._steering.begin_task("sess-1", "task-1")
    task = asyncio.create_task(asyncio.sleep(60))

    handled = await cli._handle_active_task_input(
        "Focus on the failing test first.",
        runtime=runtime,
        session_id="sess-1",
        executor_task=task,
    )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert handled is True
    assert cli._active_steering_view.history[-1] == {
        "kind": "queued_steering",
        "text": "Focus on the failing test first.",
    }


@pytest.mark.asyncio
async def test_fallback_interrupt_command_cancels_active_task():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    runtime._steering.begin_task("sess-1", "task-1")
    task = asyncio.create_task(asyncio.sleep(60))

    handled = await cli._handle_active_task_input(
        "/interrupt",
        runtime=runtime,
        session_id="sess-1",
        executor_task=task,
    )

    assert handled is True
    assert runtime.interrupt_requests == ["sess-1"]
    assert task.cancelled() or task.cancelling()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_escape_interrupts_and_keeps_pending_steering_for_immediate_follow_up():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()
    runtime = FakeRuntime()
    runtime._steering.begin_task("sess-1", "task-1")
    runtime._steering.enqueue_text("sess-1", "Focus on the failing test first.")
    task = asyncio.create_task(asyncio.sleep(60))

    handled = await cli._handle_active_task_input(
        _ESC_INTERRUPT_SENTINEL,
        runtime=runtime,
        session_id="sess-1",
        executor_task=task,
    )

    assert handled is True
    assert runtime.interrupt_requests == []
    assert task.cancelled() or task.cancelling()

    with pytest.raises(asyncio.CancelledError):
        await task

    snapshot = runtime._steering.snapshot("sess-1")
    assert snapshot["pending_count"] == 1
    assert snapshot["interrupt_requested"] is False
    assert cli._active_steering_view.history[-1] == {
        "kind": "system_notice",
        "text": "Interrupting current run to submit queued steering immediately.",
    }


@pytest.mark.asyncio
async def test_executor_cancellation_from_interrupt_does_not_escape():
    cli = AWorldCLI()
    runtime = FakeRuntime()

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            return "/interrupt"

    async def fake_executor(_prompt: str):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    cli._create_prompt_session = lambda *_args, **_kwargs: FakePromptSession()
    executor_instance = SimpleNamespace(session_id="sess-1", context=None)

    result = await cli._run_executor_with_active_steering(
        prompt="continue",
        executor=fake_executor,
        completer=object(),
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result is None
    assert runtime.interrupt_requests == ["sess-1"]


@pytest.mark.asyncio
async def test_terminal_fallback_continues_with_pending_steering_after_interrupt():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    runtime._steering.begin_task("sess-1", "task-1")
    runtime._steering.enqueue_text("sess-1", "Focus on the failing test first.")
    runtime.request_session_interrupt("sess-1")
    captured: dict[str, object] = {}

    async def fake_follow_up_runner(**kwargs):
        captured.update(kwargs)
        return "follow-up result"

    cli._run_executor_with_active_steering = fake_follow_up_runner

    continued, result = await cli._run_terminal_fallback_continuation(
        runtime=runtime,
        session_id="sess-1",
        executor=lambda _prompt: None,
        completer=object(),
        agent_name="Aworld",
        executor_instance=SimpleNamespace(session_id="sess-1", context=None),
        is_terminal=True,
    )

    assert continued is True
    assert result == "follow-up result"
    assert captured["prompt"] == (
        "Continue the current task with this additional operator steering:\n\n"
        "Interrupt requested by operator. Pause at the next safe checkpoint before continuing.\n\n"
        "1. Focus on the failing test first."
    )
    snapshot = runtime._steering.snapshot("sess-1")
    assert snapshot["pending_count"] == 0
    assert snapshot["interrupt_requested"] is False


@pytest.mark.asyncio
async def test_escape_path_requests_interrupt_and_cancels_active_task():
    cli = AWorldCLI()
    runtime = FakeRuntime()

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            return _ESC_INTERRUPT_SENTINEL

    async def fake_executor(_prompt: str):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    def fake_create_prompt_session(*_args, on_escape=None, **_kwargs):
        assert on_escape is not None
        on_escape()
        return FakePromptSession()

    cli._create_prompt_session = fake_create_prompt_session
    executor_instance = SimpleNamespace(session_id="sess-1", context=None)

    result = await cli._run_executor_with_active_steering(
        prompt="continue",
        executor=fake_executor,
        completer=object(),
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result is None
    assert runtime.interrupt_requests == ["sess-1"]


@pytest.mark.asyncio
async def test_steering_task_is_marked_inactive_after_executor_finishes():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(session_id="sess-1", context=None)

    async def fake_executor(_prompt: str):
        return "done"

    result = await cli._run_executor_with_active_steering(
        prompt="continue",
        executor=fake_executor,
        completer=None,
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=False,
    )

    assert result == "done"
    assert runtime._steering.snapshot("sess-1")["active"] is False
    assert runtime._steering.snapshot("sess-1")["pending_count"] == 0


@pytest.mark.asyncio
async def test_prompt_failure_cancels_executor_task_before_propagating():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(session_id="sess-1", context=None)
    started: asyncio.Future[asyncio.Task] = asyncio.Future()
    cancelled = asyncio.Event()

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            raise EOFError("prompt closed")

    async def fake_executor(_prompt: str):
        started.set_result(asyncio.current_task())
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    cli._create_prompt_session = lambda *_args, **_kwargs: FakePromptSession()

    with pytest.raises(EOFError, match="prompt closed"):
        await cli._run_executor_with_active_steering(
            prompt="continue",
            executor=fake_executor,
            completer=object(),
            runtime=runtime,
            agent_name="Aworld",
            executor_instance=executor_instance,
            is_terminal=True,
        )

    executor_task = await started
    assert cancelled.is_set()
    assert executor_task.cancelled() is True
    assert runtime._steering.snapshot("sess-1")["active"] is False


@pytest.mark.asyncio
async def test_executor_success_wins_when_prompt_finishes_with_error_simultaneously():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(session_id="sess-1", context=None)

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            raise EOFError("prompt closed")

    async def fake_executor(_prompt: str):
        return "done"

    cli._create_prompt_session = lambda *_args, **_kwargs: FakePromptSession()

    result = await cli._run_executor_with_active_steering(
        prompt="continue",
        executor=fake_executor,
        completer=object(),
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result == "done"
    assert runtime._steering.snapshot("sess-1")["active"] is False


@pytest.mark.asyncio
async def test_terminal_fallback_continuation_runs_follow_up_turn_for_pending_steering():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(session_id="sess-1", context=None)
    prompts: list[str] = []

    runtime._steering.enqueue_text("sess-1", "Focus on failing tests first.")

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            await asyncio.sleep(60)

    async def fake_executor(prompt: str):
        prompts.append(prompt)
        return f"result-{len(prompts)}"

    cli._create_prompt_session = lambda *_args, **_kwargs: FakePromptSession()

    result = await cli._run_executor_with_active_steering(
        prompt="Initial task",
        executor=fake_executor,
        completer=object(),
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result == "result-2"
    assert prompts[0] == "Initial task"
    assert "Focus on failing tests first." in prompts[1]


@pytest.mark.asyncio
async def test_active_run_checkpoint_pause_immediately_hands_off_to_follow_up_turn():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(session_id="sess-1", context=None)
    prompts: list[str] = []
    first_call_ready = asyncio.Event()

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            await first_call_ready.wait()
            return "Focus on the architecture first."

    async def fake_executor(prompt: str):
        prompts.append(prompt)
        if len(prompts) == 1:
            first_call_ready.set()
            while runtime._steering.snapshot("sess-1")["pending_count"] <= 0:
                await asyncio.sleep(0)
            return ""
        return "follow-up-result"

    cli._create_prompt_session = lambda *_args, **_kwargs: FakePromptSession()

    result = await cli._run_executor_with_active_steering(
        prompt="Initial task",
        executor=fake_executor,
        completer=object(),
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result == "follow-up-result"
    assert prompts[0] == "Initial task"
    assert "Focus on the architecture first." in prompts[1]
    assert runtime._steering.snapshot("sess-1")["pending_count"] == 0
    assert runtime._steering.snapshot("sess-1")["active"] is False


@pytest.mark.asyncio
async def test_escape_with_queued_steering_immediately_starts_follow_up_without_interrupt_marker():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(session_id="sess-1", context=None)
    prompts: list[str] = []
    prompt_inputs = iter(
        [
            "Focus on the architecture first.",
            _ESC_INTERRUPT_SENTINEL,
        ]
    )

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            return next(prompt_inputs)

    async def fake_executor(prompt: str):
        prompts.append(prompt)
        if len(prompts) == 1:
            while True:
                await asyncio.sleep(60)
        return "follow-up-result"

    cli._create_prompt_session = lambda *_args, **_kwargs: FakePromptSession()

    result = await cli._run_executor_with_active_steering(
        prompt="Initial task",
        executor=fake_executor,
        completer=object(),
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result == "follow-up-result"
    assert runtime.interrupt_requests == []
    assert prompts[0] == "Initial task"
    assert "Focus on the architecture first." in prompts[1]
    assert "Interrupt requested by operator." not in prompts[1]


@pytest.mark.asyncio
async def test_escape_handoff_ignores_stale_follow_up_escape_repeat():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(session_id="sess-1", context=None)
    prompts: list[str] = []
    prompt_calls = 0
    stale_escape_seen = asyncio.Event()
    discarded_sessions: list[object] = []

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            nonlocal prompt_calls
            prompt_calls += 1
            if prompt_calls == 1:
                return "Focus on the architecture first."
            if prompt_calls == 2:
                return _ESC_INTERRUPT_SENTINEL
            if prompt_calls == 3:
                stale_escape_seen.set()
                return _ESC_INTERRUPT_SENTINEL
            await asyncio.sleep(60)

    async def fake_executor(prompt: str):
        prompts.append(prompt)
        if len(prompts) == 1:
            await asyncio.sleep(60)
        await stale_escape_seen.wait()
        return "follow-up-result"

    cli._create_prompt_session = lambda *_args, **_kwargs: FakePromptSession()
    original_discard = cli._discard_prompt_session_typeahead

    def tracking_discard(session):
        discarded_sessions.append(session)
        return original_discard(session)

    cli._discard_prompt_session_typeahead = tracking_discard

    result = await cli._run_executor_with_active_steering(
        prompt="Initial task",
        executor=fake_executor,
        completer=object(),
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result == "follow-up-result"
    assert runtime.interrupt_requests == []
    assert prompt_calls >= 3
    assert discarded_sessions
    assert prompts == [
        "Initial task",
        (
            "Continue the current task with this additional operator steering:\n\n"
            "1. Focus on the architecture first."
        ),
    ]


@pytest.mark.asyncio
async def test_active_steering_clears_prompt_session_reference_after_prompt_returns():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(session_id="sess-1", context=None)
    sessions: list[object] = []
    prompts: list[str] = []

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            return "Focus on the failing test first."

    def fake_create_prompt_session(*_args, **_kwargs):
        session = FakePromptSession()
        sessions.append(session)
        cli._active_prompt_session = session
        return session

    async def fake_executor(prompt: str):
        prompts.append(prompt)
        if len(prompts) == 1:
            while runtime._steering.snapshot("sess-1")["pending_count"] <= 0:
                await asyncio.sleep(0)
            return ""
        return "follow-up-result"

    cli._create_prompt_session = fake_create_prompt_session

    result = await cli._run_executor_with_active_steering(
        prompt="Initial task",
        executor=fake_executor,
        completer=object(),
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result == "follow-up-result"
    assert sessions
    assert cli._active_prompt_session is None


@pytest.mark.asyncio
async def test_active_steering_temporarily_suppresses_executor_stream_rendering():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(
        session_id="sess-1",
        context=None,
        _suppress_interactive_stream_output=False,
    )
    observed_flags: list[bool] = []

    async def fake_executor(_prompt: str):
        observed_flags.append(executor_instance._suppress_interactive_stream_output)
        return "done"

    result = await cli._run_executor_with_active_steering(
        prompt="continue",
        executor=fake_executor,
        completer=None,
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result == "done"
    assert observed_flags == [True]
    assert executor_instance._suppress_interactive_stream_output is False


@pytest.mark.asyncio
async def test_active_steering_run_installs_and_removes_executor_event_sink():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(
        session_id="sess-1",
        context=SimpleNamespace(task_id="task-1", workspace_path="/tmp"),
        _active_steering_event_sink=None,
    )

    async def fake_executor(_prompt: str):
        assert callable(executor_instance._active_steering_event_sink)
        executor_instance._active_steering_event_sink(
            {"kind": "message_committed", "text": "Repository scan complete.", "agent_name": "Aworld"}
        )
        return "done"

    result = await cli._run_executor_with_active_steering(
        prompt="continue",
        executor=fake_executor,
        completer=None,
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result == "done"
    assert executor_instance._active_steering_event_sink is None
    assert getattr(executor_instance.context, "_aworld_cli_status_sink", None) is None


@pytest.mark.asyncio
async def test_active_steering_run_emits_task_finished_on_teardown():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    executor_instance = SimpleNamespace(
        session_id="sess-1",
        context=SimpleNamespace(task_id="task-1", workspace_path="/tmp"),
        _active_steering_event_sink=None,
    )
    seen_kinds: list[str] = []
    original_handler = cli._handle_active_steering_event

    def tracking_handler(event: dict[str, object]) -> None:
        seen_kinds.append(str(event.get("kind")))
        original_handler(event)

    cli._handle_active_steering_event = tracking_handler

    async def fake_executor(_prompt: str):
        assert callable(executor_instance._active_steering_event_sink)
        executor_instance._active_steering_event_sink(
            {"kind": "status_changed", "text": "Calling bash"}
        )
        return "done"

    result = await cli._run_executor_with_active_steering(
        prompt="continue",
        executor=fake_executor,
        completer=None,
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result == "done"
    assert "status_changed" in seen_kinds
    assert seen_kinds[-1] == "task_finished"


@pytest.mark.asyncio
async def test_active_steering_streaming_output_stays_disabled_when_event_sink_is_active(
    monkeypatch: pytest.MonkeyPatch,
):
    cli = AWorldCLI()
    runtime = FakeRuntime()
    monkeypatch.setenv("STREAM", "1")
    executor_instance = LocalAgentExecutor(Swarm(Agent(name="developer", conf=AgentConfig(skill_configs={}))))
    executor_instance.context = SimpleNamespace(task_id="task-1", workspace_path="/tmp")
    observed_streaming: list[bool] = []

    async def fake_executor(_prompt: str):
        observed_streaming.append(executor_instance._streaming_output_enabled())
        return "done"

    result = await cli._run_executor_with_active_steering(
        prompt="continue",
        executor=fake_executor,
        completer=None,
        runtime=runtime,
        agent_name="Aworld",
        executor_instance=executor_instance,
        is_terminal=True,
    )

    assert result == "done"
    assert observed_streaming == [False]
    assert executor_instance._streaming_output_enabled() is True


def test_active_steering_event_commits_message_and_tool_blocks():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()

    cli._handle_active_steering_event(
        {"kind": "message_committed", "text": "Repository scan complete.", "agent_name": "Aworld"}
    )
    cli._handle_active_steering_event(
        {"kind": "tool_calls_committed", "text": "▶ [cyan]bash[/cyan]\n   command: find . -type f | head -20"}
    )
    cli._handle_active_steering_event(
        {"kind": "tool_result_committed", "text": "⚡ [bold]terminal → bash[/bold]\n  ⎿  ./tests/test_interactive_steering.py"}
    )

    assert cli._active_steering_view.history == [
        {"kind": "assistant_message", "text": "Repository scan complete."},
        {"kind": "tool_calls", "text": "▶ [cyan]bash[/cyan]\n   command: find . -type f | head -20"},
        {"kind": "tool_result", "text": "⚡ [bold]terminal → bash[/bold]\n  ⎿  ./tests/test_interactive_steering.py"},
    ]


def test_discard_prompt_session_typeahead_flushes_input_and_clears_typeahead(
    monkeypatch: pytest.MonkeyPatch,
):
    cli = AWorldCLI()
    calls: list[str] = []

    class FakeInput:
        def flush_keys(self):
            calls.append("flush_keys")
            return []

    session = SimpleNamespace(app=SimpleNamespace(input=FakeInput()))

    monkeypatch.setattr(
        console_module,
        "clear_typeahead",
        lambda input_obj: calls.append(f"clear:{type(input_obj).__name__}"),
    )

    cli._discard_prompt_session_typeahead(session)

    assert calls == ["flush_keys", "clear:FakeInput"]


def test_active_steering_status_without_appending_history():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()

    cli._handle_active_steering_event({"kind": "status_changed", "text": "Inspecting repository"})

    assert cli._active_steering_view.status_text == "Inspecting repository"
    assert cli._active_steering_view.history == []


def test_active_steering_tool_call_started_updates_status_line():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()

    cli._handle_active_steering_event({"kind": "tool_call_started", "text": "Calling bash"})

    assert cli._active_steering_view.status_text == "Calling bash"
    assert cli._active_steering_view.history == []


def test_active_steering_task_finished_clears_status_and_appends_completion_marker(
    monkeypatch: pytest.MonkeyPatch,
):
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()
    cli._active_steering_view.started_at = 100.0
    cli._active_steering_view.status_text = "Calling tool"
    monkeypatch.setattr(console_module.time, "monotonic", lambda: 215.0)

    cli._handle_active_steering_event({"kind": "task_finished", "text": "done"})

    assert cli._active_steering_view.status_text == ""
    assert cli._active_steering_view.history[-1] == {
        "kind": "task_complete",
        "text": "Worked for 1m 55s",
    }


def test_active_steering_completion_marker_caps_visual_width():
    cli = AWorldCLI()

    marker = cli._build_active_steering_completion_marker(
        "Worked for 2m 50s",
        max_width=64,
    )

    assert "Worked for 2m 50s" in marker
    assert len(marker) == 64


def test_active_steering_completion_marker_uses_terminal_width_by_default(
    monkeypatch: pytest.MonkeyPatch,
):
    cli = AWorldCLI()
    monkeypatch.setattr(
        console_module.shutil,
        "get_terminal_size",
        lambda fallback=(0, 0): os.terminal_size((120, 40)),
    )

    marker = cli._build_active_steering_completion_marker("Worked for 9s")

    assert "Worked for 9s" in marker
    assert len(marker) == 120


def test_active_steering_deltas_do_not_append_history_directly():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()

    cli._handle_active_steering_event({"kind": "message_delta", "text": "Partial assistant text"})
    cli._handle_active_steering_event({"kind": "tool_result_delta", "text": "Partial tool output"})

    assert cli._active_steering_view.history == []


def test_active_steering_history_strips_ansi_sequences_from_committed_blocks():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()

    cli._append_active_steering_history(
        "assistant_message",
        "?[1;36mAworld?[0m\nRepository scan complete.",
        agent_name="Aworld",
    )

    assert cli._active_steering_view.history[-1] == {
        "kind": "assistant_message",
        "text": "Aworld\nRepository scan complete.",
    }


@pytest.mark.asyncio
async def test_file_parse_hook_uses_status_sink_in_active_steering_mode(monkeypatch):
    events: list[str] = []

    class DummyApplicationContext:
        workspace_path = "/tmp"

    monkeypatch.setattr(file_parse_hook_module, "ApplicationContext", DummyApplicationContext)
    hook = file_parse_hook_module.FileParseHook()
    context = DummyApplicationContext()
    context._aworld_cli_status_sink = events.append
    message = Message(
        category="agent_hook",
        payload={},
        sender="user",
        headers={"user_message": "@missing.txt", "console": None},
    )

    await hook.exec(message, context=context)

    assert any("File not found" in text or "Processing" in text for text in events)


@pytest.mark.asyncio
async def test_active_task_prompt_does_not_use_patch_stdout(monkeypatch):
    cli = AWorldCLI()
    events: list[str] = []

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            events.append("prompt")
            return "queued steering"

    def fail_patch_stdout():
        raise AssertionError("patch_stdout should not be used for active steering prompts")

    monkeypatch.setattr(console_module, "patch_stdout", fail_patch_stdout, raising=False)

    result = await cli._prompt_active_task_input(
        session=FakePromptSession(),
        runtime=None,
        agent_name="Aworld",
    )

    assert result == "queued steering"
    assert events == ["prompt"]


@pytest.mark.asyncio
async def test_active_steering_history_uses_run_in_terminal_while_prompt_is_open(
    monkeypatch: pytest.MonkeyPatch,
):
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()
    cli._active_prompt_session = object()
    calls: list[str] = []

    async def fake_run_in_terminal(func, render_cli_done=False, in_executor=False):
        calls.append("run_in_terminal")
        func()

    monkeypatch.setattr(console_module, "run_in_terminal", fake_run_in_terminal, raising=False)

    cli._append_active_steering_history(
        "assistant_message",
        "Repository scan complete.",
        agent_name="Aworld",
    )

    await asyncio.sleep(0)

    assert calls == ["run_in_terminal"]
    assert cli._active_steering_view.history[-1] == {
        "kind": "assistant_message",
        "text": "Repository scan complete.",
    }


def test_active_task_wait_text_formats_codex_like_waiting_state():
    cli = AWorldCLI()

    text = cli._build_active_task_wait_text(time.monotonic() - 149.0)

    assert text.startswith("Working")
    assert "(" in text
    assert "2m 29s" in text
    assert "Esc to interrupt" in text


def test_active_task_wait_text_animates_default_working_state(
    monkeypatch: pytest.MonkeyPatch,
):
    cli = AWorldCLI()
    monkeypatch.setattr(console_module.time, "monotonic", lambda: 100.0)

    text = cli._build_active_task_wait_text(98.6)

    assert "Working.." in text
    assert "type to steer" in text


def test_active_task_prompt_message_uses_callable_gradient_markup(
    monkeypatch: pytest.MonkeyPatch,
):
    cli = AWorldCLI()
    monkeypatch.setattr(console_module.time, "monotonic", lambda: 100.0)

    message = cli._build_active_task_prompt_message(98.6)

    assert callable(message)
    rendered = message()
    plain_text = "".join(fragment[1] for fragment in to_formatted_text(rendered))
    assert "Working.." in plain_text
    assert "Esc to interrupt" in plain_text
    assert "›" in plain_text
    assert "style fg=" in rendered.value


@pytest.mark.asyncio
async def test_active_task_prompt_keeps_status_line_and_input_anchor():
    cli = AWorldCLI()
    captured: dict[str, object] = {}

    class FakePromptSession:
        async def prompt_async(self, message="", **kwargs):
            captured["message"] = message
            captured["kwargs"] = kwargs
            return "queued steering"

    result = await cli._prompt_active_task_input(
        session=FakePromptSession(),
        runtime=None,
        agent_name="Aworld",
        wait_started_at=time.monotonic() - 5.0,
    )

    assert result == "queued steering"
    assert callable(captured["message"])
    rendered = captured["message"]()
    plain_text = "".join(fragment[1] for fragment in to_formatted_text(rendered))
    assert "Working" in plain_text
    assert "Esc to interrupt" in plain_text
    assert "›" in plain_text
    assert captured["kwargs"]["refresh_interval"] == 0.1
