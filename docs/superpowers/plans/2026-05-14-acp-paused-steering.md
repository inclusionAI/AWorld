# ACP Paused Steering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ACP treat human and approval boundaries as Happy-compatible paused steering instead of terminal errors, while keeping follow-up ACP `prompt` input on the same path and implementing everything only in `aworld`.

**Architecture:** Extend ACP turn state from `idle/running` to `idle/running/paused`, teach the ACP server to translate human and approval boundaries into ordinary `agent_message_chunk` pause notices plus resumable server-side state, and resume paused sessions by converting the next same-session prompt into steering-backed follow-up text before re-entering the existing output bridge. Keep the wire contract limited to ACP update types that Happy already consumes.

**Tech Stack:** Python, asyncio, ACP stdio server, existing steering coordinator, pytest

---

### Task 1: Extend ACP Turn State To Support Paused Sessions

**Files:**
- Modify: `aworld-cli/src/aworld_cli/acp/turn_controller.py`
- Modify: `tests/acp/test_turn_controller.py`

- [ ] **Step 1: Write the failing turn-controller tests**

Add these tests to `tests/acp/test_turn_controller.py` below the existing cases:

```python
@pytest.mark.asyncio
async def test_can_pause_running_turn_without_dropping_session_state() -> None:
    controller = TurnController()
    gate = asyncio.Event()

    async def waits() -> None:
        await gate.wait()

    task = await controller.start_turn("session-1", waits())
    controller.pause_turn("session-1")

    try:
        assert controller.has_active_turn("session-1") is True
        assert controller.is_paused("session-1") is True
    finally:
        gate.set()
        await task


@pytest.mark.asyncio
async def test_resume_paused_turn_runs_new_coroutine() -> None:
    controller = TurnController()
    first_gate = asyncio.Event()
    resumed = asyncio.Event()

    async def first_turn() -> None:
        await first_gate.wait()

    async def resumed_turn() -> None:
        resumed.set()

    task = await controller.start_turn("session-1", first_turn())
    controller.pause_turn("session-1")
    first_gate.set()
    await task

    resumed_task = await controller.resume_turn("session-1", resumed_turn())
    await resumed_task

    assert resumed.is_set() is True
    assert controller.has_active_turn("session-1") is False


@pytest.mark.asyncio
async def test_cancel_on_paused_turn_clears_paused_state() -> None:
    controller = TurnController()
    gate = asyncio.Event()

    async def waits() -> None:
        await gate.wait()

    task = await controller.start_turn("session-1", waits())
    controller.pause_turn("session-1")
    gate.set()
    await task

    result = await controller.cancel_turn("session-1")

    assert result == "cancelled"
    assert controller.has_active_turn("session-1") is False
    assert controller.is_paused("session-1") is False
```

- [ ] **Step 2: Run the turn-controller tests to verify they fail**

Run:

```bash
pytest tests/acp/test_turn_controller.py -v
```

Expected:
- FAIL because `TurnController` does not implement `pause_turn()`
- FAIL because `TurnController` does not implement `resume_turn()`
- FAIL because `TurnController` does not implement `is_paused()`

- [ ] **Step 3: Implement paused turn tracking in `turn_controller.py`**

Replace the current controller internals with an explicit record-based state model:

```python
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Literal

from .errors import AcpBusyError


TurnStatus = Literal["running", "paused"]


@dataclass
class TurnRecord:
    status: TurnStatus
    task: asyncio.Task[Any] | None = None


class TurnController:
    def __init__(self) -> None:
        self._records: dict[str, TurnRecord] = {}

    async def start_turn(
        self,
        session_id: str,
        turn_coro: Awaitable[Any],
    ) -> asyncio.Task[Any]:
        record = self._records.get(session_id)
        if record is not None and record.task is not None and record.task.done():
            if record.status == "running":
                self._records.pop(session_id, None)
                record = None

        if record is not None:
            self._close_if_possible(turn_coro)
            raise AcpBusyError(session_id)

        task = asyncio.create_task(turn_coro)
        self._records[session_id] = TurnRecord(status="running", task=task)
        task.add_done_callback(lambda finished: self._cleanup_finished_task(session_id, finished))
        return task

    async def resume_turn(
        self,
        session_id: str,
        turn_coro: Awaitable[Any],
    ) -> asyncio.Task[Any]:
        record = self._records.get(session_id)
        if record is None or record.status != "paused":
            self._close_if_possible(turn_coro)
            raise AcpBusyError(session_id)

        task = asyncio.create_task(turn_coro)
        record.status = "running"
        record.task = task
        task.add_done_callback(lambda finished: self._cleanup_finished_task(session_id, finished))
        return task

    def pause_turn(self, session_id: str) -> None:
        record = self._records.get(session_id)
        if record is None:
            return
        record.status = "paused"
        record.task = None

    async def cancel_turn(self, session_id: str) -> str:
        record = self._records.get(session_id)
        if record is None:
            return "noop"
        if record.status == "paused":
            self._records.pop(session_id, None)
            return "cancelled"

        task = record.task
        if task is None or task.done():
            self._records.pop(session_id, None)
            return "noop"

        task.cancel()
        return "cancelled"

    def has_active_turn(self, session_id: str) -> bool:
        record = self._records.get(session_id)
        if record is None:
            return False
        if record.status == "paused":
            return True
        task = record.task
        if task is None:
            return False
        if task.done():
            self._records.pop(session_id, None)
            return False
        return True

    def is_paused(self, session_id: str) -> bool:
        record = self._records.get(session_id)
        return bool(record is not None and record.status == "paused")

    def _cleanup_finished_task(self, session_id: str, task: asyncio.Task[Any]) -> None:
        record = self._records.get(session_id)
        if record is None:
            return
        if record.status == "paused":
            return
        if record.task is task:
            self._records.pop(session_id, None)

    @staticmethod
    def _close_if_possible(turn_coro: Awaitable[Any]) -> None:
        close = getattr(turn_coro, "close", None)
        if callable(close):
            close()
```

- [ ] **Step 4: Run the turn-controller tests to verify they pass**

Run:

```bash
pytest tests/acp/test_turn_controller.py -v
```

Expected:
- PASS for the existing busy/noop tests
- PASS for the new paused/resume/cancel coverage

- [ ] **Step 5: Commit**

```bash
git add tests/acp/test_turn_controller.py aworld-cli/src/aworld_cli/acp/turn_controller.py
git commit -m "feat: add paused ACP turn controller state"
```

### Task 2: Translate Human Boundaries Into Happy-Compatible Paused Steering

**Files:**
- Modify: `aworld-cli/src/aworld_cli/acp/server.py`
- Modify: `aworld-cli/src/aworld_cli/acp/self_test_bridge.py`
- Modify: `tests/acp/test_server_runtime_wiring.py`

- [ ] **Step 1: Write the failing ACP server unit tests for default paused mode**

Replace the human/approval error expectations in `tests/acp/test_server_runtime_wiring.py` with Happy-compatible paused expectations, and add a paused-resume case. Add tests shaped like these:

```python
@pytest.mark.asyncio
async def test_prompt_emits_pause_notice_for_human_intercept_in_default_mode() -> None:
    class HumanBridge:
        async def stream_outputs(self, *, record, prompt_text):
            raise AcpRequiresHumanError("Human approval/input flow is not bridged in ACP mode.")
            yield  # pragma: no cover

    server = AcpStdioServer(output_bridge=HumanBridge())
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        8,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "sessionUpdate"]

    assert response["result"]["status"] == "completed"
    assert notifications[-1]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    assert "Execution paused." in notifications[-1]["params"]["update"]["content"]["text"]


@pytest.mark.asyncio
async def test_prompt_emits_pause_notice_for_approval_boundary_in_default_mode() -> None:
    class ApprovalBridge:
        async def stream_outputs(self, *, record, prompt_text):
            raise ValueError(AWORLD_ACP_APPROVAL_UNSUPPORTED)
            yield  # pragma: no cover

    server = AcpStdioServer(output_bridge=ApprovalBridge())
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        10,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "approval path"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "sessionUpdate"]

    assert response["result"]["status"] == "completed"
    assert notifications[-1]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    assert "Execution paused." in notifications[-1]["params"]["update"]["content"]["text"]


@pytest.mark.asyncio
async def test_next_prompt_after_pause_resumes_through_steering_follow_up() -> None:
    class ResumeBridge:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def prepare_paused_resume_prompt(self, *, record, text: str) -> tuple[str, list[object]]:
            return (
                "Continue the current task with this additional operator steering:\\n\\n1. "
                + text,
                [],
            )

        async def stream_outputs(self, *, record, prompt_text, allowed_tools=None):
            self.calls.append(prompt_text)
            if len(self.calls) == 1:
                raise AcpRequiresHumanError("Human approval/input flow is not bridged in ACP mode.")
            yield MessageOutput(
                source=ModelResponse(id="resp-2", model="demo", content="resumed"),
            )

    bridge = ResumeBridge()
    server = AcpStdioServer(output_bridge=bridge)
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    first = await server._handle_prompt(
        13,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
        },
    )
    second = await server._handle_prompt(
        14,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "follow-up"}]},
        },
    )

    assert first["result"]["status"] == "completed"
    assert second["result"]["status"] == "completed"
    assert bridge.calls[1].startswith("Continue the current task with this additional operator steering:")
    assert "follow-up" in bridge.calls[1]
```

Also add one legacy-mode regression that keeps the current structured error contract:

```python
@pytest.mark.asyncio
async def test_legacy_mode_preserves_requires_human_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWORLD_ACP_LEGACY_HUMAN_ERROR_MODE", "1")

    class HumanBridge:
        async def stream_outputs(self, *, record, prompt_text):
            raise AcpRequiresHumanError("Human approval/input flow is not bridged in ACP mode.")
            yield  # pragma: no cover

    server = AcpStdioServer(output_bridge=HumanBridge())
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        18,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
        },
    )

    assert response["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
```

- [ ] **Step 2: Run the targeted ACP runtime wiring tests to verify they fail**

Run:

```bash
pytest tests/acp/test_server_runtime_wiring.py -k "requires_human or approval_unsupported or turn_error or paused or legacy_mode" -v
```

Expected:
- FAIL because `_handle_prompt()` still returns structured errors in default mode
- FAIL because `AcpStdioServer` cannot resume a paused turn from the next prompt
- FAIL because the bridge has no `prepare_paused_resume_prompt()` API

- [ ] **Step 3: Implement Happy-compatible paused steering in `server.py` and `self_test_bridge.py`**

Make these changes in `aworld-cli/src/aworld_cli/acp/server.py`:

```python
_PAUSE_NOTICE_MESSAGES = {
    AWORLD_ACP_REQUIRES_HUMAN: (
        "Execution paused. Send another prompt to steer the task forward."
    ),
    AWORLD_ACP_APPROVAL_UNSUPPORTED: (
        "Execution paused at an approval boundary. Send another prompt to steer the task forward."
    ),
}


def _legacy_human_error_mode_enabled() -> bool:
    raw = os.getenv("AWORLD_ACP_LEGACY_HUMAN_ERROR_MODE", "").strip().lower()
    return raw in {"1", "true", "yes"}
```

Inside `_handle_prompt(...)`, add a paused-session fast path before the running-turn queue path:

```python
        if self._turns.is_paused(session_id):
            resume_prompt = self._prepare_paused_resume_prompt(
                record=record,
                steering_text=prompt_text,
            )
            return await _run_streaming_prompt(
                executed_prompt_text=resume_prompt,
                resume_paused=True,
            )

        if self._turns.has_active_turn(session_id) and hasattr(self._output_bridge, "queue_steering"):
            ack_text = self._output_bridge.queue_steering(record=record, text=prompt_text)
            await self._write_session_update_for_session(
                session_id,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"text": ack_text},
                },
            )
            return self._response(request_id, {"status": "queued"})
```

Add helpers on `AcpStdioServer`:

```python
    def _prepare_paused_resume_prompt(self, *, record: AcpSessionRecord, steering_text: str) -> str:
        preparer = getattr(self._output_bridge, "prepare_paused_resume_prompt", None)
        if callable(preparer):
            follow_up_prompt, _drained_items = preparer(record=record, text=steering_text)
            return follow_up_prompt
        return (
            "Continue the current task with this additional operator steering:\\n\\n"
            f"1. {steering_text.strip()}"
        )

    async def _emit_pause_notice(self, session_id: str, *, code: str) -> None:
        text = _PAUSE_NOTICE_MESSAGES.get(
            code,
            "Execution paused. Send another prompt to steer the task forward.",
        )
        await self._write_session_update_for_session(
            session_id,
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"text": text},
            },
        )

    def _is_happy_compatible_pause_code(self, code: str) -> bool:
        return code in {AWORLD_ACP_REQUIRES_HUMAN, AWORLD_ACP_APPROVAL_UNSUPPORTED}
```

Extend `_run_streaming_prompt(...)` so it can start or resume turns:

```python
        async def _run_streaming_prompt(
            *,
            executed_prompt_text: str,
            allowed_tools: list[str] | None = None,
            resume_paused: bool = False,
        ) -> dict[str, Any]:
            state: dict[str, Any] = {}
            terminal_error: dict[str, Any] | None = None
            paused_code: str | None = None

            async def _run_turn() -> None:
                nonlocal terminal_error, paused_code
                stream_kwargs: dict[str, Any] = {
                    "record": record,
                    "prompt_text": executed_prompt_text,
                }
                if allowed_tools is not None:
                    stream_kwargs["allowed_tools"] = allowed_tools

                try:
                    async for output in self._output_bridge.stream_outputs(**stream_kwargs):
                        events = self._normalize_runtime_events(state, output)
                        for event in events:
                            if event.get("event_type") == "turn_error":
                                event_code = str(event["code"])
                                if (
                                    not _legacy_human_error_mode_enabled()
                                    and self._is_happy_compatible_pause_code(event_code)
                                ):
                                    paused_code = event_code
                                    await self._close_open_tool_lifecycles_with_error(
                                        session_id,
                                        state,
                                        code=event_code,
                                        message=str(event["message"]),
                                    )
                                    self._turns.pause_turn(session_id)
                                    await self._emit_pause_notice(session_id, code=event_code)
                                    return
                                terminal_error = event
                                await self._close_open_tool_lifecycles_with_error(
                                    session_id,
                                    state,
                                    code=event_code,
                                    message=str(event["message"]),
                                )
                                return
                            if event.get("event_type") == "tool_start":
                                tool_call_id = event.get("tool_call_id")
                                if isinstance(tool_call_id, str):
                                    state[f"tool_input::{tool_call_id}"] = event.get("raw_input")
                            if event.get("event_type") == "tool_end":
                                tool_name = event.get("tool_name")
                                tool_call_id = event.get("tool_call_id")
                                if isinstance(tool_name, str) and isinstance(tool_call_id, str):
                                    state[f"tool_closed::{tool_name}"] = tool_call_id
                                self._cron_bridge.bind_from_tool_result(
                                    session_id=session_id,
                                    tool_name=tool_name if isinstance(tool_name, str) else None,
                                    payload=event.get("raw_output"),
                                    tool_input=(
                                        state.get(f"tool_input::{tool_call_id}")
                                        if isinstance(tool_call_id, str)
                                        else None
                                    ),
                                )
                            update = map_runtime_event_to_session_update(session_id, event)
                            await self._write_session_update(update)
                except AcpRequiresHumanError:
                    if _legacy_human_error_mode_enabled():
                        raise
                    paused_code = AWORLD_ACP_REQUIRES_HUMAN
                    await self._close_open_tool_lifecycles_with_error(
                        session_id,
                        state,
                        code=AWORLD_ACP_REQUIRES_HUMAN,
                        message=self._error_detail_message(AWORLD_ACP_REQUIRES_HUMAN),
                    )
                    self._turns.pause_turn(session_id)
                    await self._emit_pause_notice(session_id, code=AWORLD_ACP_REQUIRES_HUMAN)
                except ValueError as exc:
                    detail = self._known_error_detail(str(exc))
                    if (
                        detail is not None
                        and not _legacy_human_error_mode_enabled()
                        and self._is_happy_compatible_pause_code(detail.code)
                    ):
                        paused_code = detail.code
                        await self._close_open_tool_lifecycles_with_error(
                            session_id,
                            state,
                            code=detail.code,
                            message=detail.message,
                        )
                        self._turns.pause_turn(session_id)
                        await self._emit_pause_notice(session_id, code=detail.code)
                        return
                    raise
```

When launching the turn, choose `resume_turn()` for paused sessions:

```python
            if resume_paused:
                task = await self._turns.resume_turn(session_id, _run_turn())
            else:
                task = await self._turns.start_turn(session_id, _run_turn())
```

After `await task`, short-circuit paused default mode before old error handling:

```python
            if paused_code is not None and not _legacy_human_error_mode_enabled():
                return self._response(request_id, {"status": "completed"})
```

In `aworld-cli/src/aworld_cli/acp/self_test_bridge.py`, add a Happy-compatible paused-resume helper:

```python
    def prepare_paused_resume_prompt(self, *, record, text: str) -> tuple[str, list[object]]:
        self._steering.begin_task(record.aworld_session_id, f"self-test-{record.aworld_session_id}")
        self._steering.enqueue_text(record.aworld_session_id, text)
        follow_up_prompt, drained_items, _interrupt_requested = self._steering.consume_terminal_fallback(
            record.aworld_session_id
        )
        if not follow_up_prompt:
            raise ValueError("expected paused steering follow-up prompt")
        return follow_up_prompt, drained_items
```

- [ ] **Step 4: Run the targeted ACP runtime wiring tests to verify they pass**

Run:

```bash
pytest tests/acp/test_server_runtime_wiring.py -k "requires_human or approval_unsupported or turn_error or paused or legacy_mode" -v
```

Expected:
- PASS with default mode producing pause notices and `result.status == "completed"`
- PASS with paused follow-up prompt resuming through steering text
- PASS with legacy mode preserving the current structured errors

- [ ] **Step 5: Commit**

```bash
git add \
  aworld-cli/src/aworld_cli/acp/server.py \
  aworld-cli/src/aworld_cli/acp/self_test_bridge.py \
  tests/acp/test_server_runtime_wiring.py
git commit -m "feat: pause ACP human boundaries for steering"
```

### Task 3: Update Stdio And Validation Coverage For Happy-Compatible Pause Semantics

**Files:**
- Modify: `tests/acp/test_server_stdio.py`
- Modify: `aworld-cli/src/aworld_cli/acp/validation.py`
- Modify: `aworld-cli/src/aworld_cli/acp/validation_profiles.py`

- [ ] **Step 1: Write the failing stdio and validation expectations**

Update the stdio tests that currently expect terminal human-boundary errors so they instead expect:
- existing `tool_call` / `tool_call_update` closure
- one `agent_message_chunk` pause notice
- a non-error prompt response in default mode

For `tests/acp/test_server_stdio.py`, change assertions like this:

```python
        first = _read_json_line(proc)
        second = _read_json_line(proc)
        third = _read_json_line(proc)
        fourth = _read_json_line(proc)

        assert first["method"] == "sessionUpdate"
        assert first["params"]["update"]["sessionUpdate"] == "tool_call"
        assert second["method"] == "sessionUpdate"
        assert second["params"]["update"]["sessionUpdate"] == "tool_call_update"
        assert second["params"]["update"]["status"] == "failed"
        assert third["method"] == "sessionUpdate"
        assert third["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
        assert "Execution paused." in third["params"]["update"]["content"]["text"]
        assert fourth["id"] == 3
        assert fourth["result"]["status"] == "completed"
```

Add a paused-resume stdio case using the self-test bridge:

```python
@pytest.mark.asyncio
async def test_acp_server_resumes_paused_prompt_with_follow_up_steering() -> None:
    proc = await _spawn_async_acp_server({"AWORLD_ACP_SELF_TEST_BRIDGE": "1"})
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.write('{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n')
        proc.stdin.flush()
        _ = await _read_json_line_async(proc)
        new_session = await _read_json_line_async(proc)
        session_id = new_session["result"]["sessionId"]
        first_prompt = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "prompt",
            "params": {
                "sessionId": session_id,
                "prompt": {"content": [{"type": "text", "text": SELF_TEST_TURN_ERROR_PROMPT}]},
            },
        }
        follow_up = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "prompt",
            "params": {
                "sessionId": session_id,
                "prompt": {"content": [{"type": "text", "text": "continue anyway"}]},
            },
        }
        await _write_json_line_async(proc, first_prompt)
        session_updates: list[dict] = []
        seen_by_id: dict[int, dict] = {}

        while 3 not in seen_by_id:
            item = await _read_json_line_async(proc)
            if item.get("method") == "sessionUpdate":
                session_updates.append(item)
                continue
            seen_by_id[item["id"]] = item

        await _write_json_line_async(proc, follow_up)
        while 4 not in seen_by_id:
            item = await _read_json_line_async(proc)
            if item.get("method") == "sessionUpdate":
                session_updates.append(item)
                continue
            seen_by_id[item["id"]] = item
        assert seen_pause_notice is True
        assert seen_by_id[3]["result"]["status"] == "completed"
        assert seen_by_id[4]["result"]["status"] == "completed"
        assert any(
            item.get("params", {}).get("update", {}).get("content", {}).get("text") == "continue anyway"
            or item.get("params", {}).get("update", {}).get("content", {}).get("text") == "resumed"
            for item in session_updates
        )
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
```

In `aworld-cli/src/aworld_cli/acp/validation.py`, replace the `turn_error_terminal` / `prompt_busy_rejected` assumptions with paused-compatible checks:

```python
        cases.append(
            {
                "id": "turn_error_pauses_for_steering",
                "ok": (
                    turn_error_start.get("params", {}).get("update", {}).get("sessionUpdate") == "tool_call"
                    and turn_error_end.get("params", {}).get("update", {}).get("sessionUpdate") == "tool_call_update"
                    and pause_notice.get("params", {}).get("update", {}).get("sessionUpdate") == "agent_message_chunk"
                    and "Execution paused." in pause_notice.get("params", {}).get("update", {}).get("content", {}).get("text", "")
                    and turn_error_result.get("result", {}).get("status") == "completed"
                ),
                "detail": {
                    "start": turn_error_start,
                    "end": turn_error_end,
                    "pause": pause_notice,
                    "result": turn_error_result,
                },
            }
        )
```

- [ ] **Step 2: Run the stdio and validation tests to verify they fail**

Run:

```bash
pytest tests/acp/test_server_stdio.py -k "requires_human or turn_error or queues_busy_prompt or self_test_bridge" -v
pytest tests/acp/test_validation.py -v
```

Expected:
- FAIL because stdio still emits terminal errors for human/approval boundaries
- FAIL because validation still expects `turn_error_terminal`

- [ ] **Step 3: Implement the test fixture and validation updates**

Update `aworld-cli/src/aworld_cli/acp/validation.py` to rename and re-check the paused case IDs:

```python
REQUIRED_PHASE1_CASE_IDS = (
    "initialize_handshake",
    "new_session_usable",
    "prompt_visible_text",
    "cancel_idle_noop",
    "tool_lifecycle_closes",
    "turn_error_pauses_for_steering",
    "turn_error_suppresses_followup_events",
    "post_turn_error_session_continues",
    "final_text_fallback",
    "prompt_busy_queued",
    "cancel_active_terminal",
    "stdout_protocol_only",
    "stderr_diagnostics_only",
)
```

Adjust the busy-session validation to match the existing steering acknowledgement path:

```python
        cases.append(
            {
                "id": "prompt_busy_queued",
                "ok": (
                    seen_by_id[8].get("result", {}).get("status") == "queued"
                    and any(
                        item.get("params", {}).get("update", {}).get("sessionUpdate") == "agent_message_chunk"
                        and item.get("params", {}).get("update", {}).get("content", {}).get("text")
                        == "Steering captured. Applying at next checkpoint."
                        for item in busy_session_updates
                    )
                ),
                "detail": {
                    "queued": seen_by_id[8],
                    "updates": busy_session_updates,
                },
            }
        )
```

No profile values need to change in `validation_profiles.py`; keep using `SELF_TEST_TURN_ERROR_PROMPT`, but update the validation logic around its expected outcome.

- [ ] **Step 4: Run the stdio and validation tests to verify they pass**

Run:

```bash
pytest tests/acp/test_server_stdio.py -k "requires_human or turn_error or queues_busy_prompt or self_test_bridge" -v
pytest tests/acp/test_validation.py -v
```

Expected:
- PASS with default stdio mode emitting pause notices and non-error prompt completion
- PASS with validation case IDs and expectations aligned to paused steering

- [ ] **Step 5: Commit**

```bash
git add \
  tests/acp/test_server_stdio.py \
  aworld-cli/src/aworld_cli/acp/validation.py \
  aworld-cli/src/aworld_cli/acp/validation_profiles.py
git commit -m "test: cover ACP paused steering protocol"
```

### Task 4: Full Verification And Final Sweep

**Files:**
- Modify: none expected
- Test: `tests/acp/test_turn_controller.py`
- Test: `tests/acp/test_server_runtime_wiring.py`
- Test: `tests/acp/test_server_stdio.py`
- Test: `tests/acp/test_validation.py`

- [ ] **Step 1: Run the focused ACP suite**

Run:

```bash
pytest \
  tests/acp/test_turn_controller.py \
  tests/acp/test_server_runtime_wiring.py \
  tests/acp/test_server_stdio.py \
  tests/acp/test_validation.py -v
```

Expected:
- all targeted ACP paused-steering tests PASS
- no remaining expectations of terminal human-boundary errors in default mode

- [ ] **Step 2: Run gateway regression coverage that should remain unchanged**

Run:

```bash
pytest tests/gateway/test_router.py -k "steering or queued" -v
```

Expected:
- PASS
- no regression in existing gateway steering semantics

- [ ] **Step 3: Sanity-check the new ACP compatibility switch behavior**

Run:

```bash
AWORLD_ACP_LEGACY_HUMAN_ERROR_MODE=1 pytest tests/acp/test_server_runtime_wiring.py -k "legacy_mode or requires_human or approval_unsupported" -v
```

Expected:
- PASS
- legacy mode still returns structured retryable errors

- [ ] **Step 4: Commit the verification-only changes if any**

```bash
git status --short
```

Expected:
- no source changes from verification

- [ ] **Step 5: Completion checkpoint**

```bash
git log --oneline -4
```

Expected:
- three feature/test commits from Tasks 1-3 are visible
- working tree is clean before merge or PR work
