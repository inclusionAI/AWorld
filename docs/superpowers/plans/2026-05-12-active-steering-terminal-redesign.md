# Active Steering Terminal Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework local `aworld-cli` active steering so a running task uses a stable transcript area, a single runtime status line, and a fixed bottom prompt while executor output is committed in readable blocks instead of streamed directly into the shared terminal surface.

**Architecture:** `AWorldCLI` owns the active steering terminal surface and renders only transcript history, one status line, and the bottom input prompt. `LocalAgentExecutor` emits structured active-steering events and uses a small commit buffer in `executors/stream.py` to sanitize, summarize, and commit assistant/tool output at natural boundaries. Ordinary non-active-steering chat keeps the current streaming path.

**Tech Stack:** Python, `prompt_toolkit`, Rich, pytest

---

## File Structure

- `aworld-cli/src/aworld_cli/console.py`
  Active steering view state, fixed bottom prompt, status line rendering, structured event consumption, and executor event-sink lifecycle.
- `aworld-cli/src/aworld_cli/executors/local.py`
  Active steering event emission, chunk/message/tool-result buffering, commit-boundary orchestration, and status updates.
- `aworld-cli/src/aworld_cli/executors/stream.py`
  New active steering commit buffer with ANSI sanitization and B-granularity tool-result summarization.
- `aworld-cli/src/aworld_cli/executors/file_parse_hook.py`
  Route file-parse progress into the active steering status sink instead of directly writing to the console.
- `tests/test_interactive_steering.py`
  Console ownership, event handling, active steering prompt behavior, hook routing, and end-to-end steering-loop regressions.
- `tests/executors/test_stream.py`
  Active steering commit-buffer sanitization and summarization behavior.
- `tests/hooks/test_cli_steering_before_llm_hook.py`
  Executor event-mode regressions that should stay stable while active steering suppresses direct streaming.

### Task 1: Cut `patch_stdout` From The Active Steering Prompt Path

**Files:**
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_active_task_prompt_does_not_use_patch_stdout(monkeypatch):
    cli = AWorldCLI()
    patch_calls: list[str] = []

    class FakePromptSession:
        async def prompt_async(self, *_args, **_kwargs):
            return "queued steering"

    def fail_patch_stdout():
        patch_calls.append("called")
        raise AssertionError("patch_stdout should not be used in active steering mode")

    monkeypatch.setattr(console_module, "patch_stdout", fail_patch_stdout)

    result = await cli._prompt_active_task_input(
        session=FakePromptSession(),
        runtime=None,
        agent_name="Aworld",
    )

    assert result == "queued steering"
    assert patch_calls == []


@pytest.mark.asyncio
async def test_active_task_prompt_keeps_status_line_and_input_anchor(monkeypatch):
    cli = AWorldCLI()
    captured: dict[str, object] = {}

    class FakePromptSession:
        async def prompt_async(self, message="", **kwargs):
            captured["message"] = message
            captured["kwargs"] = kwargs
            return "queued steering"

    monkeypatch.setattr(
        console_module,
        "patch_stdout",
        lambda: (_ for _ in ()).throw(AssertionError("patch_stdout should not be used")),
    )

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interactive_steering.py -q -k "does_not_use_patch_stdout or keeps_status_line_and_input_anchor"`
Expected: FAIL because `_prompt_active_task_input()` still wraps `session.prompt_async(...)` in `patch_stdout()`.

- [ ] **Step 3: Write minimal implementation**

```python
async def _prompt_active_task_input(
    self,
    *,
    session: PromptSession,
    runtime: Any,
    agent_name: str,
    wait_started_at: float | None = None,
) -> str:
    prompt_kwargs = self._build_prompt_kwargs(
        runtime,
        agent_name=agent_name,
        mode="Steering",
    )
    prompt_message = self._build_active_task_prompt_message(wait_started_at)
    prompt_kwargs["reserve_space_for_menu"] = 0
    return await session.prompt_async(prompt_message, **prompt_kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interactive_steering.py -q -k "does_not_use_patch_stdout or keeps_status_line_and_input_anchor"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/console.py tests/test_interactive_steering.py
git commit -m "fix: stop using patch_stdout in active steering prompts"
```

### Task 2: Expand The Console-Side Active Steering Event Protocol

**Files:**
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_status_changed_updates_status_without_appending_history():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()

    cli._handle_active_steering_event({"kind": "status_changed", "text": "Calling bash"})

    assert cli._active_steering_view.status_text == "Calling bash"
    assert cli._active_steering_view.history == []


def test_task_finished_clears_active_steering_status_line():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()
    cli._set_active_steering_status("Calling bash")

    cli._handle_active_steering_event({"kind": "task_finished"})

    assert cli._active_steering_view.status_text == ""


def test_message_and_tool_deltas_do_not_append_history_directly():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()

    cli._handle_active_steering_event({"kind": "message_delta", "text": "Repo "})
    cli._handle_active_steering_event({"kind": "tool_result_delta", "text": "line 1"})

    assert cli._active_steering_view.history == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interactive_steering.py -q -k "status_without_appending_history or clears_active_steering_status_line or deltas_do_not_append_history_directly"`
Expected: FAIL because `_handle_active_steering_event()` does not yet handle `task_finished`, and delta events are not explicitly ignored/documented.

- [ ] **Step 3: Write minimal implementation**

```python
def _handle_active_steering_event(self, event: dict[str, Any]) -> None:
    if not isinstance(event, dict):
        return

    kind = str(event.get("kind") or "").strip()
    text = str(event.get("text") or "").strip()
    if not kind:
        return

    if kind == "status_changed":
        self._set_active_steering_status(text)
        return

    if kind == "tool_call_started":
        self._set_active_steering_status(text or "Calling tool")
        return

    if kind == "task_finished":
        self._set_active_steering_status("")
        return

    if kind in {"message_delta", "tool_result_delta"}:
        return

    mapping = {
        "message_committed": "assistant_message",
        "tool_calls_committed": "tool_calls",
        "tool_result_committed": "tool_result",
        "system_notice": "system_notice",
        "error": "error",
    }
    history_kind = mapping.get(kind)
    if history_kind is None:
        return

    self._append_active_steering_history(
        history_kind,
        text,
        agent_name=str(event.get("agent_name") or "").strip() or None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interactive_steering.py -q -k "status_without_appending_history or clears_active_steering_status_line or deltas_do_not_append_history_directly"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/console.py tests/test_interactive_steering.py
git commit -m "feat: expand active steering console event handling"
```

### Task 3: Add An Active Steering Commit Buffer With Sanitization And B-Granularity Summaries

**Files:**
- Modify: `aworld-cli/src/aworld_cli/executors/stream.py`
- Test: `tests/executors/test_stream.py`

- [ ] **Step 1: Write the failing tests**

```python
from aworld_cli.executors.stream import ActiveSteeringCommitBuffer


def test_active_steering_commit_buffer_keeps_short_tool_results_full():
    buffer = ActiveSteeringCommitBuffer(max_full_result_lines=4, max_summary_lines=2)

    event = buffer.commit_tool_result(["line 1", "line 2"], exit_code=0)

    assert event == {
        "kind": "tool_result_committed",
        "text": "line 1\nline 2",
    }


def test_active_steering_commit_buffer_summarizes_long_tool_results():
    buffer = ActiveSteeringCommitBuffer(max_full_result_lines=3, max_summary_lines=2)

    event = buffer.commit_tool_result(
        [f"line {index}" for index in range(1, 7)],
        exit_code=1,
    )

    assert event["kind"] == "tool_result_committed"
    assert "Exit code: 1" in event["text"]
    assert "line 1" in event["text"]
    assert "line 2" in event["text"]
    assert "... (4 more lines)" in event["text"]


def test_active_steering_commit_buffer_sanitizes_message_text():
    buffer = ActiveSteeringCommitBuffer()

    event = buffer.commit_message("?[1;36mAworld?[0m\nRepository scan complete.", agent_name="Aworld")

    assert event == {
        "kind": "message_committed",
        "text": "Aworld\nRepository scan complete.",
        "agent_name": "Aworld",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/executors/test_stream.py -q -k "commit_buffer_keeps_short_tool_results_full or commit_buffer_summarizes_long_tool_results or commit_buffer_sanitizes_message_text"`
Expected: FAIL because `ActiveSteeringCommitBuffer` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass
class ActiveSteeringCommitBuffer:
    max_full_result_lines: int = 8
    max_summary_lines: int = 4
    message_chunks: list[str] = field(default_factory=list)

    def append_message_delta(self, text: str) -> None:
        normalized = self._sanitize(text)
        if normalized:
            self.message_chunks.append(normalized)

    def commit_message(self, text: str | None = None, *, agent_name: str | None = None) -> dict[str, Any] | None:
        source = text if text is not None else "".join(self.message_chunks)
        normalized = self._sanitize(source)
        self.message_chunks.clear()
        if not normalized:
            return None
        event = {"kind": "message_committed", "text": normalized}
        if agent_name:
            event["agent_name"] = agent_name
        return event

    def commit_tool_result(self, lines: list[str], *, exit_code: int | None = None) -> dict[str, Any] | None:
        cleaned = [self._sanitize(line) for line in lines if self._sanitize(line)]
        if not cleaned:
            return None
        if len(cleaned) <= self.max_full_result_lines:
            return {"kind": "tool_result_committed", "text": "\n".join(cleaned)}

        summary_lines: list[str] = []
        if exit_code not in (None, 0):
            summary_lines.append(f"Exit code: {exit_code}")
        summary_lines.extend(cleaned[: self.max_summary_lines])
        remaining = len(cleaned) - self.max_summary_lines
        summary_lines.append(f"... ({remaining} more lines)")
        return {"kind": "tool_result_committed", "text": "\n".join(summary_lines)}

    def _sanitize(self, text: str) -> str:
        normalized = str(text or "")
        normalized = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", normalized)
        normalized = re.sub(r"\?\[[0-9;?]*[A-Za-z]", "", normalized)
        normalized = normalized.replace("\t", "    ")
        lines = [line.rstrip() for line in normalized.splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines).strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/executors/test_stream.py -q -k "commit_buffer_keeps_short_tool_results_full or commit_buffer_summarizes_long_tool_results or commit_buffer_sanitizes_message_text"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/executors/stream.py tests/executors/test_stream.py
git commit -m "feat: add active steering commit buffer"
```

### Task 4: Route Local Executor Streaming Through The Commit Buffer And Structured Events

**Files:**
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
- Test: `tests/hooks/test_cli_steering_before_llm_hook.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_local_executor_flushes_buffered_message_chunks_to_commit_event():
    agent = Agent(name="developer", conf=AgentConfig(skill_configs={}))
    executor = LocalAgentExecutor(Swarm(agent))
    executor._active_steering_commit_buffer = ActiveSteeringCommitBuffer()
    events: list[dict[str, str]] = []
    executor._active_steering_event_sink = events.append

    executor._buffer_active_steering_message_chunk("Repo ")
    executor._buffer_active_steering_message_chunk("scan complete.")
    executor._flush_active_steering_message_buffer(agent_name="Aworld")

    assert events == [
        {
            "kind": "message_committed",
            "text": "Repo scan complete.",
            "agent_name": "Aworld",
        }
    ]


def test_local_executor_emits_b_granularity_tool_result_summary():
    agent = Agent(name="developer", conf=AgentConfig(skill_configs={}))
    executor = LocalAgentExecutor(Swarm(agent))
    executor._active_steering_commit_buffer = ActiveSteeringCommitBuffer(
        max_full_result_lines=3,
        max_summary_lines=2,
    )
    events: list[dict[str, str]] = []
    executor._active_steering_event_sink = events.append

    executor._emit_active_steering_tool_result_lines(
        [f"line {index}" for index in range(1, 7)],
        exit_code=1,
    )

    assert events[-1]["kind"] == "tool_result_committed"
    assert "Exit code: 1" in events[-1]["text"]
    assert "... (4 more lines)" in events[-1]["text"]


def test_local_executor_streaming_output_is_disabled_when_event_sink_is_active(monkeypatch):
    agent = Agent(name="developer", conf=AgentConfig(skill_configs={}))
    executor = LocalAgentExecutor(Swarm(agent))

    monkeypatch.setenv("STREAM", "1")
    executor._active_steering_event_sink = lambda _event: None

    assert executor._streaming_output_enabled() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/hooks/test_cli_steering_before_llm_hook.py tests/test_interactive_steering.py -q -k "flushes_buffered_message_chunks_to_commit_event or b_granularity_tool_result_summary or disabled_when_event_sink_is_active"`
Expected: FAIL because `_buffer_active_steering_message_chunk`, `_flush_active_steering_message_buffer`, and `_emit_active_steering_tool_result_lines` do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
from .stream import ActiveSteeringCommitBuffer, StreamDisplayConfig, StreamDisplayController, _print_tool_result_lines


def _active_steering_buffer(self) -> ActiveSteeringCommitBuffer:
    buffer = getattr(self, "_active_steering_commit_buffer", None)
    if buffer is None:
        buffer = ActiveSteeringCommitBuffer()
        self._active_steering_commit_buffer = buffer
    return buffer


def _buffer_active_steering_message_chunk(self, text: str) -> None:
    self._active_steering_buffer().append_message_delta(text)


def _flush_active_steering_message_buffer(self, *, agent_name: str | None = None) -> None:
    event = self._active_steering_buffer().commit_message(agent_name=agent_name)
    if event is not None:
        self._emit_active_steering_event(**event)


def _emit_active_steering_tool_result_lines(self, lines: list[str], *, exit_code: int | None = None) -> None:
    event = self._active_steering_buffer().commit_tool_result(lines, exit_code=exit_code)
    if event is not None:
        self._emit_active_steering_event(**event)
```

Then thread these helpers into the stream loop:

```python
if active_event_mode and isinstance(output, ChunkOutput):
    chunk = output.data if hasattr(output, "data") else getattr(output, "data", None)
    if chunk and getattr(chunk, "content", None):
        self._buffer_active_steering_message_chunk(chunk.content)
    continue

if active_event_mode and isinstance(output, MessageOutput):
    self._flush_active_steering_message_buffer(agent_name=current_agent_name or "Assistant")
    response_text = str(output.response) if hasattr(output, "response") and output.response else ""
    committed = self._active_steering_buffer().commit_message(response_text, agent_name=current_agent_name or "Assistant")
    if committed is not None:
        self._emit_active_steering_event(**committed)
    if tool_calls:
        self._emit_active_steering_event("tool_calls_committed", text="\n".join(self._format_tool_calls_display_lines(tool_calls)))
        self._emit_active_steering_event("tool_call_started", text=f"Calling {current_tool_name}")
    else:
        self._emit_active_steering_status("Working")
    continue

if active_event_mode and isinstance(output, ToolResultOutput):
    tr_lines = self._format_tool_result_display_lines(output)
    self._emit_active_steering_tool_result_lines(tr_lines, exit_code=getattr(output, "code", None))
    self._emit_active_steering_status("Working")
    continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/hooks/test_cli_steering_before_llm_hook.py tests/test_interactive_steering.py -q -k "flushes_buffered_message_chunks_to_commit_event or b_granularity_tool_result_summary or disabled_when_event_sink_is_active"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/executors/local.py tests/hooks/test_cli_steering_before_llm_hook.py tests/test_interactive_steering.py
git commit -m "feat: route active steering executor output through commit buffer"
```

### Task 5: Route File Parse Progress To The Status Sink Instead Of The Transcript

**Files:**
- Modify: `aworld-cli/src/aworld_cli/executors/file_parse_hook.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_file_parse_hook_prefers_status_sink_over_console(monkeypatch):
    status_events: list[str] = []
    console_events: list[str] = []

    class DummyApplicationContext:
        workspace_path = "/tmp"

    class FakeConsole:
        def print(self, text):
            console_events.append(str(text))

    monkeypatch.setattr(file_parse_hook_module, "ApplicationContext", DummyApplicationContext)
    hook = file_parse_hook_module.FileParseHook()
    context = DummyApplicationContext()
    context._aworld_cli_status_sink = status_events.append
    message = Message(
        category="agent_hook",
        payload={},
        sender="user",
        headers={"user_message": "@missing.txt", "console": FakeConsole()},
    )

    await hook.exec(message, context=context)

    assert status_events
    assert console_events == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interactive_steering.py -q -k file_parse_hook_prefers_status_sink_over_console`
Expected: FAIL if `FileParseHook` still falls back to `console.print(...)` even when `_aworld_cli_status_sink` is available.

- [ ] **Step 3: Write minimal implementation**

```python
status_sink = getattr(context, "_aworld_cli_status_sink", None) if context is not None else None


def emit_status(text: str) -> None:
    normalized = _strip_rich_markup(text)
    if callable(status_sink):
        status_sink(normalized)
        return
    if console:
        console.print(text)
```

Then keep using `emit_status(...)` for progress branches such as:

```python
emit_status(f"[dim]📁 [FileParseHook] Processing {len(valid_matches)} file reference(s)[/dim]")
emit_status(f"[yellow]⚠️ [FileParseHook] Failed to download remote file {file_ref}: {e}[/yellow]")
emit_status(f"[red]❌ [FileParseHook] File not found: {file_ref}[/red]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interactive_steering.py -q -k file_parse_hook_prefers_status_sink_over_console`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/executors/file_parse_hook.py tests/test_interactive_steering.py
git commit -m "fix: send file parse progress to active steering status sink"
```

### Task 6: Close The Lifecycle Loop And Run Focused Regression Coverage

**Files:**
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_active_steering_run_clears_status_and_restores_executor_sinks():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    context = SimpleNamespace(task_id="task-1", workspace_path="/tmp")
    executor_instance = SimpleNamespace(
        session_id="sess-1",
        context=context,
        _active_steering_event_sink=None,
        _suppress_interactive_loading_status=False,
        _suppress_interactive_stream_output=False,
    )

    async def fake_executor(_prompt: str):
        sink = executor_instance._active_steering_event_sink
        sink({"kind": "status_changed", "text": "Calling bash"})
        sink({"kind": "message_committed", "text": "Repository scan complete.", "agent_name": "Aworld"})
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
    assert getattr(context, "_aworld_cli_status_sink", None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interactive_steering.py -q -k clears_status_and_restores_executor_sinks`
Expected: FAIL if the active steering loop does not emit a `task_finished`-style cleanup path before unbinding the sinks.

- [ ] **Step 3: Write minimal implementation**

```python
finally:
    if self._active_steering_view is not None:
        self._handle_active_steering_event({"kind": "task_finished"})
    steering = getattr(runtime, "_steering", None) if runtime is not None else None
    if steering is not None and session_id:
        try:
            steering.end_task(session_id, clear_pending=True)
        except Exception:
            pass
    if executor_instance is not None and previous_loading_suppressed is not None:
        executor_instance._suppress_interactive_loading_status = previous_loading_suppressed
    if executor_instance is not None and previous_stream_suppressed is not None:
        executor_instance._suppress_interactive_stream_output = previous_stream_suppressed
    if executor_instance is not None and is_terminal:
        executor_instance._active_steering_event_sink = previous_event_sink
        context = getattr(executor_instance, "context", None)
        if context is not None:
            context._aworld_cli_status_sink = previous_status_sink
    self._active_steering_view = None
    self._current_executor_task = None
```

- [ ] **Step 4: Run focused regression commands**

Run:

```bash
pytest tests/executors/test_stream.py tests/core/test_cli_steering_coordinator.py tests/hooks/test_cli_steering_before_llm_hook.py tests/test_interactive_steering.py tests/plugins/test_plugin_commands.py tests/plugins/test_plugin_hud.py tests/plugins/test_runtime_hud_snapshot.py -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/console.py aworld-cli/src/aworld_cli/executors/local.py tests/test_interactive_steering.py
git commit -m "fix: finalize active steering lifecycle cleanup"
```

## Manual Validation

- Run a real local interactive task in a terminal with active steering enabled.
- Verify the terminal always shows:
  - transcript history above
  - one runtime status line
  - one fixed bottom prompt
- Verify natural steering text queues cleanly without corrupting the prompt.
- Verify `Esc` or `/interrupt` still interrupts the running task.
- Verify long tool results commit as a short readable summary with key lines instead of flooding the transcript.
- Verify ANSI/control-sequence remnants like `?[1;36m` do not appear in committed history.
