# Active Steering Terminal Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework local `aworld-cli` active steering so a running task uses a stable transcript area, a single runtime status line, and a bottom prompt that stays clearly interactive.

**Architecture:** Add an active-steering presentation mode at the CLI layer and route executor output through committed display events instead of live terminal streaming while the active steering loop is running. Keep ordinary chat mode and ACP behavior unchanged by gating the new path behind local active steering only.

**Tech Stack:** Python, `prompt_toolkit`, Rich, pytest

---

### Task 1: Add Active Steering Presentation State And Tests

**Files:**
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_active_steering_feedback_is_recorded_as_committed_history():
    cli = AWorldCLI()

    cli._active_steering_view = cli._create_active_steering_view()
    cli._append_active_steering_history("system_notice", "Steering queued for the next checkpoint.")

    assert cli._active_steering_view.history == [
        {"kind": "system_notice", "text": "Steering queued for the next checkpoint."}
    ]


def test_active_steering_status_line_tracks_runtime_summary():
    cli = AWorldCLI()

    cli._active_steering_view = cli._create_active_steering_view()
    cli._set_active_steering_status("Calling bash")

    assert cli._active_steering_view.status_text == "Calling bash"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interactive_steering.py -q -k "committed_history or status_line_tracks_runtime_summary"`
Expected: FAIL because `_create_active_steering_view`, `_append_active_steering_history`, or `_set_active_steering_status` do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass, field


@dataclass
class ActiveSteeringView:
    history: list[dict[str, str]] = field(default_factory=list)
    status_text: str = ""


def _create_active_steering_view(self) -> ActiveSteeringView:
    return ActiveSteeringView()


def _append_active_steering_history(self, kind: str, text: str) -> None:
    if self._active_steering_view is None or not str(text).strip():
        return
    self._active_steering_view.history.append({"kind": kind, "text": str(text).strip()})


def _set_active_steering_status(self, text: str) -> None:
    if self._active_steering_view is None:
        return
    self._active_steering_view.status_text = str(text).strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interactive_steering.py -q -k "committed_history or status_line_tracks_runtime_summary"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/console.py tests/test_interactive_steering.py
git commit -m "feat: add active steering presentation state"
```

### Task 2: Move Steering Acknowledgements Into Committed Transcript Blocks

**Files:**
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_plain_text_steering_ack_appends_committed_history():
    cli = AWorldCLI()
    runtime = FakeRuntime()
    runtime._steering.begin_task("sess-1", "task-1")
    cli._active_steering_view = cli._create_active_steering_view()
    task = asyncio.create_task(asyncio.sleep(60))

    await cli._handle_active_task_input(
        "Focus on failing tests first.",
        runtime=runtime,
        session_id="sess-1",
        executor_task=task,
    )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cli._active_steering_view.history[-1] == {
        "kind": "system_notice",
        "text": "Steering queued for the next checkpoint.",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interactive_steering.py -q -k steering_ack_appends_committed_history`
Expected: FAIL because the steering acknowledgement is still printed directly rather than appended into presentation history.

- [ ] **Step 3: Write minimal implementation**

```python
if self._active_steering_view is not None:
    self._append_active_steering_history(
        "system_notice",
        "Steering queued for the next checkpoint.",
    )
else:
    self.console.print("[dim]Steering queued for the next checkpoint.[/dim]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interactive_steering.py -q -k steering_ack_appends_committed_history`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/console.py tests/test_interactive_steering.py
git commit -m "feat: commit steering acknowledgements into active history"
```

### Task 3: Add Executor Event Sink For Active Steering Mode

**Files:**
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
- Test: `tests/hooks/test_cli_steering_before_llm_hook.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_local_executor_reports_status_updates_to_active_steering_sink():
    agent = Agent(name="developer", conf=AgentConfig(skill_configs={}))
    executor = LocalAgentExecutor(Swarm(agent))
    events = []

    executor._active_steering_event_sink = events.append
    executor._emit_active_steering_event("status_changed", text="Calling bash")

    assert events == [{"kind": "status_changed", "text": "Calling bash"}]


def test_local_executor_streaming_output_is_disabled_when_event_sink_is_active(monkeypatch):
    agent = Agent(name="developer", conf=AgentConfig(skill_configs={}))
    executor = LocalAgentExecutor(Swarm(agent))

    monkeypatch.setenv("STREAM", "1")
    executor._active_steering_event_sink = lambda _event: None

    assert executor._streaming_output_enabled() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/hooks/test_cli_steering_before_llm_hook.py tests/test_interactive_steering.py -q -k "event_sink or disabled_when_event_sink_is_active"`
Expected: FAIL because `_emit_active_steering_event` does not exist and `_streaming_output_enabled` does not yet honor event-sink mode.

- [ ] **Step 3: Write minimal implementation**

```python
def _emit_active_steering_event(self, kind: str, **payload) -> None:
    sink = getattr(self, "_active_steering_event_sink", None)
    if sink is None:
        return
    sink({"kind": kind, **payload})


def _streaming_output_enabled(self) -> bool:
    stream_on = os.environ.get("STREAM", "0").lower() in ("1", "true", "yes")
    if not stream_on:
        return False
    if getattr(self, "_active_steering_event_sink", None) is not None:
        return False
    return not bool(getattr(self, "_suppress_interactive_stream_output", False))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/hooks/test_cli_steering_before_llm_hook.py tests/test_interactive_steering.py -q -k "event_sink or disabled_when_event_sink_is_active"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/executors/local.py tests/hooks/test_cli_steering_before_llm_hook.py tests/test_interactive_steering.py
git commit -m "feat: add active steering executor event sink"
```

### Task 4: Convert Active Steering Executor Output Into Committed Blocks

**Files:**
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_active_steering_events_commit_message_and_tool_blocks():
    cli = AWorldCLI()
    cli._active_steering_view = cli._create_active_steering_view()

    cli._handle_active_steering_event({"kind": "message_committed", "text": "Repository scan complete."})
    cli._handle_active_steering_event({"kind": "tool_calls_committed", "text": "bash: find . -type f | head -20"})
    cli._handle_active_steering_event({"kind": "tool_result_committed", "text": "./tests/test_interactive_steering.py"})

    assert cli._active_steering_view.history == [
        {"kind": "assistant_message", "text": "Repository scan complete."},
        {"kind": "tool_calls", "text": "bash: find . -type f | head -20"},
        {"kind": "tool_result", "text": "./tests/test_interactive_steering.py"},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interactive_steering.py -q -k commit_message_and_tool_blocks`
Expected: FAIL because the CLI does not yet know how to consume executor events into committed history.

- [ ] **Step 3: Write minimal implementation**

```python
def _handle_active_steering_event(self, event: dict[str, Any]) -> None:
    kind = str(event.get("kind") or "").strip()
    text = str(event.get("text") or "").strip()
    if kind == "status_changed":
        self._set_active_steering_status(text)
        return
    mapping = {
        "message_committed": "assistant_message",
        "tool_calls_committed": "tool_calls",
        "tool_result_committed": "tool_result",
        "system_notice": "system_notice",
        "error": "error",
    }
    history_kind = mapping.get(kind)
    if history_kind and text:
        self._append_active_steering_history(history_kind, text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interactive_steering.py -q -k commit_message_and_tool_blocks`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/console.py aworld-cli/src/aworld_cli/executors/local.py tests/test_interactive_steering.py
git commit -m "feat: commit active steering executor blocks through console"
```

### Task 5: Suppress Noisy Hook Console Output During Active Steering

**Files:**
- Modify: `aworld-cli/src/aworld_cli/executors/file_parse_hook.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_file_parse_hook_uses_status_sink_in_active_steering_mode():
    hook = FileParseHook()
    events = []
    context = ApplicationContext()
    context.workspace_path = "/tmp"
    context._aworld_cli_status_sink = lambda text: events.append(text)
    message = Message(
        category="agent_hook",
        payload={},
        sender="user",
        headers={"user_message": "@missing.txt", "console": None},
    )

    await hook.exec(message, context=context)

    assert any("FileParseHook" in text for text in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interactive_steering.py -q -k file_parse_hook_uses_status_sink`
Expected: FAIL because `FileParseHook` still writes directly to console and does not use a context-level status sink.

- [ ] **Step 3: Write minimal implementation**

```python
status_sink = getattr(context, "_aworld_cli_status_sink", None)


def emit_status(text: str) -> None:
    if callable(status_sink):
        status_sink(text)
    elif console:
        console.print(text)
```

Replace direct `console.print(...)` calls in active-status-worthy branches with `emit_status(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interactive_steering.py -q -k file_parse_hook_uses_status_sink`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/executors/file_parse_hook.py tests/test_interactive_steering.py
git commit -m "feat: route file parse progress into active steering status sink"
```

### Task 6: Wire Active Steering Mode End-To-End

**Files:**
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
- Test: `tests/test_interactive_steering.py`

- [ ] **Step 1: Write the failing test**

```python
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
        executor_instance._active_steering_event_sink({"kind": "message_committed", "text": "done"})
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interactive_steering.py -q -k installs_and_removes_executor_event_sink`
Expected: FAIL because the active steering loop does not yet install an event sink onto the executor instance.

- [ ] **Step 3: Write minimal implementation**

```python
self._active_steering_view = self._create_active_steering_view()

if executor_instance is not None:
    executor_instance._active_steering_event_sink = self._handle_active_steering_event
    if getattr(executor_instance, "context", None) is not None:
        executor_instance.context._aworld_cli_status_sink = self._set_active_steering_status

try:
    ...
finally:
    if executor_instance is not None:
        executor_instance._active_steering_event_sink = None
        if getattr(executor_instance, "context", None) is not None:
            executor_instance.context._aworld_cli_status_sink = None
    self._active_steering_view = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interactive_steering.py -q -k installs_and_removes_executor_event_sink`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/console.py aworld-cli/src/aworld_cli/executors/local.py tests/test_interactive_steering.py
git commit -m "feat: wire active steering event mode end to end"
```

### Task 7: Regression Verification And Manual Validation Notes

**Files:**
- Modify: `docs/superpowers/specs/2026-05-12-active-steering-terminal-redesign.md`

- [ ] **Step 1: Run focused regression tests**

Run:

```bash
pytest tests/executors/test_stream.py tests/core/test_cli_steering_coordinator.py tests/hooks/test_cli_steering_before_llm_hook.py tests/test_interactive_steering.py tests/plugins/test_plugin_commands.py tests/plugins/test_plugin_hud.py tests/plugins/test_runtime_hud_snapshot.py -q
```

Expected: PASS

- [ ] **Step 2: Add manual validation notes to the spec**

Append a short section summarizing:

```markdown
## Manual Validation

- run a local interactive task
- verify bottom prompt remains obviously interactive
- verify steering acknowledgement appears as a committed block
- verify tool output appends as readable blocks
- verify `Esc` still interrupts
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-12-active-steering-terminal-redesign.md
git commit -m "docs: add validation notes for active steering redesign"
```
