import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import aworld_cli.console as console_module
import aworld_cli.executors.file_parse_hook as file_parse_hook_module
from aworld.core.event.base import Message
from aworld_cli.console import AWorldCLI, _ESC_INTERRUPT_SENTINEL
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

    assert "Calling bash" in text
    assert "type to steer" in text


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
        "kind": "system_notice",
        "text": "Steering queued for the next checkpoint.",
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


def test_active_task_wait_text_formats_codex_like_waiting_state():
    cli = AWorldCLI()

    text = cli._build_active_task_wait_text(time.monotonic() - 149.0)

    assert "Working (" in text
    assert "2m 29s" in text
    assert "Esc to interrupt" in text


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
    assert "Working (" in str(captured["message"])
    assert "Esc to interrupt" in str(captured["message"])
    assert "›" in str(captured["message"])
