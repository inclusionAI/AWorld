import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import aworld_cli.console as console_module
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
async def test_active_task_prompt_uses_patch_stdout_for_concurrent_executor_output(monkeypatch):
    cli = AWorldCLI()
    events: list[str] = []

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            events.append("prompt")
            return "queued steering"

    class FakePatchStdout:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")
            return False

    monkeypatch.setattr(console_module, "patch_stdout", lambda: FakePatchStdout())

    result = await cli._prompt_active_task_input(
        session=FakePromptSession(),
        runtime=None,
        agent_name="Aworld",
    )

    assert result == "queued steering"
    assert events == ["enter", "prompt", "exit"]


def test_active_task_wait_text_formats_codex_like_waiting_state():
    cli = AWorldCLI()

    text = cli._build_active_task_wait_text(time.monotonic() - 149.0)

    assert "Waiting for background task" in text
    assert "2m 29s" in text
    assert "Esc to interrupt" in text


@pytest.mark.asyncio
async def test_active_task_prompt_uses_waiting_placeholder_with_minimal_input_anchor(monkeypatch):
    cli = AWorldCLI()
    captured: dict[str, object] = {}

    class FakePromptSession:
        async def prompt_async(self, message="", **kwargs):
            captured["message"] = message
            captured["kwargs"] = kwargs
            return "queued steering"

    class FakePatchStdout:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(console_module, "patch_stdout", lambda: FakePatchStdout())

    result = await cli._prompt_active_task_input(
        session=FakePromptSession(),
        runtime=None,
        agent_name="Aworld",
        wait_started_at=time.monotonic() - 5.0,
    )

    assert result == "queued steering"
    assert "Steer" not in str(captured["message"])
    assert str(captured["message"]).strip() != ""

    placeholder = captured["kwargs"]["placeholder"]
    placeholder_text = placeholder() if callable(placeholder) else placeholder
    assert "Waiting for background task" in str(placeholder_text)
    assert "Esc to interrupt" in str(placeholder_text)
