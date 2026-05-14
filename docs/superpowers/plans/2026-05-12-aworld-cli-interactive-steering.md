# AWorld CLI Interactive Steering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add local interactive steering to `aworld-cli` so plain text entered during an active executor-backed task is queued for the next safe checkpoint, `Esc` interrupts the active local task in terminal-capable sessions, and `/interrupt` remains as the compatibility fallback.

**Architecture:** Introduce a session-scoped steering coordinator in `aworld-cli`, teach `console.py` to keep accepting input while an executor task runs, and inject queued steering at `BEFORE_LLM_CALL` through a dedicated hook that reads transient steering state attached to the runtime/context. Use a built-in plugin only for the operator control surface (`/interrupt`) and HUD lines; keep queue ownership and continuation decisions in runtime/console/executor code.

**Tech Stack:** Python, asyncio, prompt_toolkit, pytest, aworld hook framework, aworld-cli plugin/HUD framework, OpenSpec-aligned CLI behavior.

---

## File Structure

- Create: `aworld-cli/src/aworld_cli/steering/__init__.py`
- Create: `aworld-cli/src/aworld_cli/steering/coordinator.py`
- Create: `aworld-cli/src/aworld_cli/executors/steering_before_llm_hook.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/.aworld-plugin/plugin.json`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/__init__.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/commands/interrupt.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/hud/status.py`
- Create: `tests/core/test_cli_steering_coordinator.py`
- Create: `tests/hooks/test_cli_steering_before_llm_hook.py`
- Create: `tests/test_interactive_steering.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/base.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
- Modify: `aworld-cli/src/aworld_cli/executors/__init__.py`
- Modify: `tests/plugins/test_plugin_commands.py`
- Modify: `tests/plugins/test_plugin_hud.py`

### Task 1: Add The Session-Scoped Steering Coordinator

**Files:**
- Create: `aworld-cli/src/aworld_cli/steering/__init__.py`
- Create: `aworld-cli/src/aworld_cli/steering/coordinator.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/base.py`
- Test: `tests/core/test_cli_steering_coordinator.py`

- [x] **Step 1: Write the failing coordinator tests**

Create `tests/core/test_cli_steering_coordinator.py` with focused behavior tests like:

```python
from aworld_cli.steering.coordinator import SteeringCoordinator


def test_enqueue_and_drain_fifo():
    coordinator = SteeringCoordinator()
    coordinator.begin_task(session_id="sess-1", task_id="task-1")

    coordinator.enqueue_text("sess-1", "Focus on failing tests first.")
    coordinator.enqueue_text("sess-1", "Avoid refactoring unrelated files.")

    drained = coordinator.drain_for_checkpoint("sess-1")

    assert [item.text for item in drained] == [
        "Focus on failing tests first.",
        "Avoid refactoring unrelated files.",
    ]
    assert coordinator.snapshot("sess-1")["pending_count"] == 0


def test_interrupt_flag_and_terminal_fallback_prompt_reset():
    coordinator = SteeringCoordinator()
    coordinator.begin_task(session_id="sess-1", task_id="task-1")
    coordinator.enqueue_text("sess-1", "Re-run the failing test before editing code.")
    coordinator.request_interrupt("sess-1")

    prompt = coordinator.consume_terminal_fallback_prompt("sess-1")

    assert "Re-run the failing test before editing code." in prompt
    assert coordinator.snapshot("sess-1")["interrupt_requested"] is False
    assert coordinator.snapshot("sess-1")["pending_count"] == 0
```

- [x] **Step 2: Run the focused coordinator tests and verify they fail**

Run:

```bash
pytest tests/core/test_cli_steering_coordinator.py -q
```

Expected:

```text
FAIL because aworld_cli.steering.coordinator does not exist yet.
```

- [x] **Step 3: Implement the coordinator and runtime accessors**

Create `aworld-cli/src/aworld_cli/steering/coordinator.py` and wire it into `BaseCliRuntime` with code shaped like:

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class SteeringInput:
    sequence: int
    text: str
    created_at: str


@dataclass
class SteeringSessionState:
    active_task_id: str | None = None
    steerable: bool = False
    interrupt_requested: bool = False
    next_sequence: int = 1
    pending_inputs: list[SteeringInput] = field(default_factory=list)


class SteeringCoordinator:
    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions: dict[str, SteeringSessionState] = {}

    def begin_task(self, session_id: str, task_id: str, *, steerable: bool = True) -> None:
        with self._lock:
            state = self._sessions.setdefault(session_id, SteeringSessionState())
            state.active_task_id = task_id
            state.steerable = steerable
            state.interrupt_requested = False

    def enqueue_text(self, session_id: str, text: str) -> SteeringInput:
        normalized = str(text).strip()
        if not normalized:
            raise ValueError("steering text must not be empty")
        with self._lock:
            state = self._sessions.setdefault(session_id, SteeringSessionState())
            item = SteeringInput(
                sequence=state.next_sequence,
                text=normalized,
                created_at=_utcnow(),
            )
            state.next_sequence += 1
            state.pending_inputs.append(item)
            return item

    def request_interrupt(self, session_id: str) -> bool:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or not state.steerable or not state.active_task_id:
                return False
            state.interrupt_requested = True
            return True

    def snapshot(self, session_id: str) -> dict[str, object]:
        with self._lock:
            state = self._sessions.get(session_id) or SteeringSessionState()
            excerpt = state.pending_inputs[-1].text if state.pending_inputs else None
            return {
                "active": bool(state.steerable and state.active_task_id),
                "task_id": state.active_task_id,
                "pending_count": len(state.pending_inputs),
                "interrupt_requested": state.interrupt_requested,
                "last_steer_excerpt": excerpt,
            }

    def drain_for_checkpoint(self, session_id: str) -> list[SteeringInput]:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or not state.pending_inputs:
                return []
            drained = list(state.pending_inputs)
            state.pending_inputs.clear()
            return drained

    def consume_terminal_fallback_prompt(self, session_id: str) -> str | None:
        drained = self.drain_for_checkpoint(session_id)
        if not drained:
            return None
        lines = [
            "Continue the current task with this additional operator steering:",
            "",
        ]
        for index, item in enumerate(drained, start=1):
            lines.append(f"{index}. {item.text}")
        with self._lock:
            state = self._sessions.get(session_id)
            if state is not None:
                state.interrupt_requested = False
        return "\n".join(lines).strip()
```

Add runtime methods in `aworld-cli/src/aworld_cli/runtime/base.py` like:

```python
from ..steering.coordinator import SteeringCoordinator

self._steering = SteeringCoordinator()

def steering_snapshot(self, session_id: str | None) -> dict[str, Any]:
    return self._steering.snapshot(session_id) if session_id else {}

def request_session_interrupt(self, session_id: str | None) -> bool:
    return bool(session_id) and self._steering.request_interrupt(session_id)
```

- [x] **Step 4: Run the coordinator tests and verify they pass**

Run:

```bash
pytest tests/core/test_cli_steering_coordinator.py -q
```

Expected:

```text
2 passed
```

- [x] **Step 5: Commit**

```bash
git add \
  aworld-cli/src/aworld_cli/steering/__init__.py \
  aworld-cli/src/aworld_cli/steering/coordinator.py \
  aworld-cli/src/aworld_cli/runtime/base.py \
  tests/core/test_cli_steering_coordinator.py
git commit -m "feat: add cli steering coordinator"
```

### Task 2: Refactor The Interactive Session Loop For Concurrent Input

**Files:**
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/base.py`
- Test: `tests/test_interactive_steering.py`

- [x] **Step 1: Write the failing console-routing tests**

Create `tests/test_interactive_steering.py` with helper-oriented tests like:

```python
import asyncio
from types import SimpleNamespace

import pytest

from aworld_cli.console import AWorldCLI
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

    assert handled is True
    snapshot = runtime._steering.snapshot("sess-1")
    assert snapshot["pending_count"] == 1
    assert snapshot["last_steer_excerpt"] == "Focus on the failing test first."


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
```

- [x] **Step 2: Run the focused console tests and verify they fail**

Run:

```bash
pytest tests/test_interactive_steering.py -q
```

Expected:

```text
FAIL because AWorldCLI does not yet expose active steering helpers.
```

- [x] **Step 3: Implement the active-task input helpers and `Esc`-aware prompt session**

Update `aworld-cli/src/aworld_cli/console.py` along these lines:

```python
_ESC_INTERRUPT_SENTINEL = "__aworld_interrupt__"


def _create_prompt_session(self, completer: Completer, *, on_escape=None) -> PromptSession:
    history_path = Path.home() / ".aworld" / "cli_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    kb = KeyBindings()

    if on_escape is not None:
        @kb.add("escape")
        def _interrupt(event):
            on_escape()
            event.app.exit(result=_ESC_INTERRUPT_SENTINEL)

    session = PromptSession(
        completer=completer,
        complete_while_typing=True,
        history=FileHistory(str(history_path)),
        key_bindings=kb,
    )
    self._active_prompt_session = session
    return session


async def _handle_active_task_input(self, user_input, *, runtime, session_id, executor_task) -> bool:
    normalized = (user_input or "").strip()
    if not normalized:
        return True
    if normalized == _ESC_INTERRUPT_SENTINEL or normalized == "/interrupt":
        runtime.request_session_interrupt(session_id)
        executor_task.cancel()
        self.console.print("[dim]Interrupt requested.[/dim]")
        return True
    if normalized.startswith("/"):
        self.console.print("[yellow]Only /interrupt is available while steering is active.[/yellow]")
        return True
    runtime._steering.enqueue_text(session_id, normalized)
    self.console.print("[dim]Steering queued for the next checkpoint.[/dim]")
    return True
```

- [x] **Step 4: Replace the blocking executor call in `run_chat_session` with an active steering loop**

Refactor the executor path in `run_chat_session` to use a helper like:

```python
async def _run_steerable_prompt_session(self, *, completer, prompt_text, executor, runtime, executor_instance, agent_name):
    session_id = getattr(executor_instance, "session_id", None)
    executor_task = asyncio.create_task(
        self._run_executor_prompt(
            prompt_text,
            executor,
            executor_instance=executor_instance,
        )
    )
    self._current_executor_task = executor_task
    try:
        while True:
            if executor_task.done():
                return await executor_task
            active_session = self._create_prompt_session(
                completer,
                on_escape=lambda: runtime.request_session_interrupt(session_id),
            )
            user_input = await active_session.prompt_async(
                HTML("<b><cyan>Steer</cyan></b>: "),
                **self._build_prompt_kwargs(runtime, agent_name=agent_name, mode="Steering"),
            )
            await self._handle_active_task_input(
                user_input,
                runtime=runtime,
                session_id=session_id,
                executor_task=executor_task,
            )
    finally:
        self._current_executor_task = None
```

Keep the old idle input path for the normal top-level prompt. Do not let `Esc` on the idle prompt become an interrupt request.

- [x] **Step 5: Run the focused console tests and verify they pass**

Run:

```bash
pytest tests/test_interactive_steering.py -q
```

Expected:

```text
2 passed
```

- [x] **Step 6: Commit**

```bash
git add \
  aworld-cli/src/aworld_cli/console.py \
  aworld-cli/src/aworld_cli/runtime/base.py \
  tests/test_interactive_steering.py
git commit -m "feat: allow steering input during active cli tasks"
```

### Task 3: Inject Steering At `BEFORE_LLM_CALL` And Add Terminal Fallback Continuation

**Files:**
- Create: `aworld-cli/src/aworld_cli/executors/steering_before_llm_hook.py`
- Modify: `aworld-cli/src/aworld_cli/executors/__init__.py`
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Test: `tests/hooks/test_cli_steering_before_llm_hook.py`
- Test: `tests/test_interactive_steering.py`

- [x] **Step 1: Write the failing hook test**

Create `tests/hooks/test_cli_steering_before_llm_hook.py` with a focused test like:

```python
from types import SimpleNamespace

import pytest

from aworld.core.event.base import Message
from aworld_cli.executors.steering_before_llm_hook import SteeringBeforeLlmHook
from aworld_cli.steering.coordinator import SteeringCoordinator


@pytest.mark.asyncio
async def test_before_llm_hook_appends_pending_steering_messages():
    coordinator = SteeringCoordinator()
    coordinator.begin_task("sess-1", "task-1")
    coordinator.enqueue_text("sess-1", "Focus on failing tests first.")
    context = SimpleNamespace(session_id="sess-1", _aworld_cli_steering=coordinator)
    hook = SteeringBeforeLlmHook()
    message = Message(
        category="agent_hook",
        payload={
            "messages": [
                {"role": "user", "content": "Initial task"},
            ]
        },
        sender="llm_model",
        headers={},
    )

    result = await hook.exec(message, context=context)

    updated = result.headers["updated_input"]
    assert updated[-1] == {"role": "user", "content": "Focus on failing tests first."}
    assert coordinator.snapshot("sess-1")["pending_count"] == 0
```

- [x] **Step 2: Run the focused hook test and verify it fails**

Run:

```bash
pytest tests/hooks/test_cli_steering_before_llm_hook.py -q
```

Expected:

```text
FAIL because SteeringBeforeLlmHook is not registered or does not exist yet.
```

- [x] **Step 3: Implement the hook and attach steering state to task contexts**

Create `aworld-cli/src/aworld_cli/executors/steering_before_llm_hook.py`:

```python
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PreLLMCallHook


@HookFactory.register(name="SteeringBeforeLlmHook")
class SteeringBeforeLlmHook(PreLLMCallHook):
    async def exec(self, message: Message, context=None) -> Message:
        steering = getattr(context, "_aworld_cli_steering", None)
        if steering is None or context is None:
            return message

        payload = message.payload if isinstance(message.payload, dict) else {}
        messages = list(payload.get("messages") or [])
        drained = steering.drain_for_checkpoint(context.session_id)
        if not drained:
            return message

        updated_messages = list(messages)
        for item in drained:
            updated_messages.append({"role": "user", "content": item.text})

        message.headers["updated_input"] = updated_messages
        return message
```

Import the hook in `aworld-cli/src/aworld_cli/executors/__init__.py`:

```python
from .steering_before_llm_hook import SteeringBeforeLlmHook  # noqa: F401
```

Attach the coordinator in `LocalAgentExecutor._build_task` or immediately after context creation:

```python
runtime = getattr(self, "_base_runtime", None)
if runtime is not None and getattr(runtime, "_steering", None) is not None:
    context._aworld_cli_steering = runtime._steering
```

- [x] **Step 4: Add terminal fallback continuation after task completion**

After `_run_executor_prompt(...)` returns in the active steering path, consume a fallback prompt when needed:

```python
async def _run_terminal_fallback_continuation(self, *, runtime, session_id, executor, executor_instance, agent_name):
    follow_up_prompt = runtime._steering.consume_terminal_fallback_prompt(session_id)
    if not follow_up_prompt:
        return None
    self.console.print("[dim]Applying queued steering in a follow-up turn.[/dim]")
    return await self._run_executor_prompt(
        follow_up_prompt,
        executor,
        executor_instance=executor_instance,
    )
```

Call this helper when the active steerable executor task exits with pending steering still queued.

- [x] **Step 5: Run the focused hook and steering tests**

Run:

```bash
pytest tests/hooks/test_cli_steering_before_llm_hook.py tests/test_interactive_steering.py -q
```

Expected:

```text
All focused steering hook and fallback tests pass.
```

- [x] **Step 6: Commit**

```bash
git add \
  aworld-cli/src/aworld_cli/executors/steering_before_llm_hook.py \
  aworld-cli/src/aworld_cli/executors/__init__.py \
  aworld-cli/src/aworld_cli/executors/local.py \
  aworld-cli/src/aworld_cli/console.py \
  tests/hooks/test_cli_steering_before_llm_hook.py \
  tests/test_interactive_steering.py
git commit -m "feat: apply queued steering before llm calls"
```

### Task 4: Add The Steering Plugin Command And HUD Surface

**Files:**
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/.aworld-plugin/plugin.json`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/__init__.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/commands/interrupt.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/hud/status.py`
- Modify: `tests/plugins/test_plugin_commands.py`
- Modify: `tests/plugins/test_plugin_hud.py`

- [x] **Step 1: Add failing plugin command and HUD tests**

Extend `tests/plugins/test_plugin_commands.py` with a built-in plugin registration check like:

```python
def test_interrupt_plugin_command_registers():
    from aworld_cli.core.command_system import CommandRegistry
    from aworld_cli.runtime.cli import CliRuntime

    runtime = CliRuntime()
    runtime.refresh_plugin_framework()

    command = CommandRegistry.get("interrupt")

    assert command is not None
    assert command.command_type == "tool"
```

Extend `tests/plugins/test_plugin_hud.py` with a HUD assertion like:

```python
def test_steering_hud_renders_pending_count():
    from aworld_cli.runtime.cli import CliRuntime

    runtime = CliRuntime()
    runtime.update_hud_snapshot(
        steering={
            "active": True,
            "pending_count": 2,
            "interrupt_requested": False,
        }
    )

    lines = runtime.get_hud_lines(runtime.build_hud_context(agent_name="Aworld", mode="Chat"))

    assert any("Steering: active" in line.text for line in lines)
    assert any("Pending: 2" in line.text for line in lines)
```

- [x] **Step 2: Run the focused plugin tests and verify they fail**

Run:

```bash
pytest tests/plugins/test_plugin_commands.py tests/plugins/test_plugin_hud.py -q
```

Expected:

```text
FAIL because the steering plugin and HUD provider do not exist yet.
```

- [x] **Step 3: Implement the built-in plugin**

Create `aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/.aworld-plugin/plugin.json`:

```json
{
  "id": "steering-cli",
  "name": "steering-cli",
  "version": "1.0.0",
  "entrypoints": {
    "commands": [
      {
        "id": "interrupt",
        "name": "interrupt",
        "description": "Interrupt the active steerable task",
        "target": "commands/interrupt.py",
        "scope": "session"
      }
    ],
    "hud": [
      {
        "id": "steering-status",
        "target": "hud/status.py",
        "scope": "session",
        "metadata": {
          "surface": "bottom_toolbar"
        }
      }
    ]
  }
}
```

Create `commands/interrupt.py`:

```python
from aworld_cli.core.command_system import CommandContext
from aworld_cli.plugin_capabilities.commands import PluginBoundCommand


class InterruptCommand(PluginBoundCommand):
    @property
    def command_type(self) -> str:
        return "tool"

    async def execute(self, context: CommandContext) -> str:
        runtime = getattr(context, "runtime", None)
        session_id = context.session_id
        if runtime is None or not hasattr(runtime, "request_session_interrupt"):
            return "Interrupt control is unavailable."
        requested = runtime.request_session_interrupt(session_id)
        return "Interrupt requested." if requested else "No active steerable task."


def build_command(plugin, entrypoint):
    return InterruptCommand(plugin, entrypoint)
```

Create `hud/status.py`:

```python
def render_lines(context, plugin_state):
    steering = context.get("steering", {})
    if not steering:
        return []
    active = "active" if steering.get("active") else "idle"
    pending = steering.get("pending_count", 0)
    interrupted = "yes" if steering.get("interrupt_requested") else "no"
    return [
        {
            "section": "activity",
            "priority": 25,
            "segments": [
                f"Steering: {active}",
                f"Pending: {pending}",
                f"Interrupt: {interrupted}",
            ],
        }
    ]
```

- [x] **Step 4: Publish steering snapshots to the HUD context**

Update runtime/console/executor code so HUD snapshots include a `steering` bucket:

```python
runtime.update_hud_snapshot(
    steering={
        "active": snapshot["active"],
        "pending_count": snapshot["pending_count"],
        "interrupt_requested": snapshot["interrupt_requested"],
        "last_steer_excerpt": snapshot.get("last_steer_excerpt"),
    }
)
```

Refresh that bucket when:

- a steerable task starts
- plain steering text is queued
- an interrupt is requested
- the active task finishes and steering state is cleared

- [x] **Step 5: Run the focused plugin tests and verify they pass**

Run:

```bash
pytest tests/plugins/test_plugin_commands.py tests/plugins/test_plugin_hud.py -q
```

Expected:

```text
Focused plugin command and HUD tests pass with the new built-in steering plugin.
```

- [x] **Step 6: Commit**

```bash
git add \
  aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/.aworld-plugin/plugin.json \
  aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/__init__.py \
  aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/commands/interrupt.py \
  aworld-cli/src/aworld_cli/builtin_plugins/steering_cli/hud/status.py \
  aworld-cli/src/aworld_cli/runtime/base.py \
  aworld-cli/src/aworld_cli/console.py \
  tests/plugins/test_plugin_commands.py \
  tests/plugins/test_plugin_hud.py
git commit -m "feat: add steering plugin command and hud"
```

### Task 5: Run The Final Focused Regression Set

**Files:**
- Reference: `tests/core/test_cli_steering_coordinator.py`
- Reference: `tests/hooks/test_cli_steering_before_llm_hook.py`
- Reference: `tests/test_interactive_steering.py`
- Reference: `tests/plugins/test_plugin_commands.py`
- Reference: `tests/plugins/test_plugin_hud.py`
- Reference: `openspec/changes/2026-05-12-aworld-cli-interactive-steering/specs/cli-experience/spec.md`

- [x] **Step 1: Run the full focused steering regression suite**

Run:

```bash
pytest \
  tests/core/test_cli_steering_coordinator.py \
  tests/hooks/test_cli_steering_before_llm_hook.py \
  tests/test_interactive_steering.py \
  tests/plugins/test_plugin_commands.py \
  tests/plugins/test_plugin_hud.py -q
```

Expected:

```text
All steering-specific focused tests pass.
```

- [x] **Step 2: Re-run OpenSpec validation**

Run:

```bash
openspec validate 2026-05-12-aworld-cli-interactive-steering
```

Expected:

```text
Change '2026-05-12-aworld-cli-interactive-steering' is valid
```

- [x] **Step 3: Run one broader CLI regression that exercises command registration**

Run:

```bash
pytest tests/test_slash_commands.py tests/test_plugin_cli_entrypoint.py -q
```

Expected:

```text
The broader CLI command registration regression remains green.
```

- [x] **Step 4: Commit**

```bash
git add \
  docs/superpowers/plans/2026-05-12-aworld-cli-interactive-steering.md \
  tests/core/test_cli_steering_coordinator.py \
  tests/hooks/test_cli_steering_before_llm_hook.py \
  tests/test_interactive_steering.py \
  tests/plugins/test_plugin_commands.py \
  tests/plugins/test_plugin_hud.py
git commit -m "test: validate cli steering flow"
```
