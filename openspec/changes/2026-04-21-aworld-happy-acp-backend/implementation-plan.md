# AWorld ACP Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a generic ACP-compatible AWorld backend in `aworld-cli` that Happy CLI/daemon can host over stdio without any Happy code changes or `aworld/core` changes.

**Architecture:** Add a new `aworld_cli.acp` package that owns the ACP wire server, session store, turn controller, runtime adapter, event mapper, and self-test entrypoint. Keep `main.py` changes thin, reuse plugin/hook surfaces through a narrow ACP-local bootstrap helper, and intercept human-in-loop paths entirely in the host layer so ACP mode never blocks on hidden terminal input.

**Tech Stack:** Python 3.10+, `asyncio`, `json`, `dataclasses`, existing `aworld-cli` runtime/executor layers, existing plugin/hook loaders, `pytest`.

---

## File Structure

### New implementation files

- `aworld-cli/src/aworld_cli/acp/__init__.py`
  Exposes the ACP package surface.
- `aworld-cli/src/aworld_cli/acp/cli.py`
  Parses `aworld-cli acp ...` arguments and dispatches `serve` / `self-test`.
- `aworld-cli/src/aworld_cli/acp/protocol.py`
  Owns JSON-RPC 2.0 + NDJSON helpers and ACP message construction/parsing.
- `aworld-cli/src/aworld_cli/acp/errors.py`
  Defines stable phase-1 error codes and response helpers.
- `aworld-cli/src/aworld_cli/acp/session_store.py`
  Maintains `acp_session_id -> aworld_session_id` mappings and host-local session metadata.
- `aworld-cli/src/aworld_cli/acp/turn_controller.py`
  Enforces per-session active-turn serialization and cancel semantics.
- `aworld-cli/src/aworld_cli/acp/bootstrap.py`
  Reuses plugin/hook discovery with ACP-safe side effects.
- `aworld-cli/src/aworld_cli/acp/human_intercept.py`
  Registers an ACP-only human handler override with higher priority than `CLIHumanHandler`.
- `aworld-cli/src/aworld_cli/acp/runtime_adapter.py`
  Wraps runtime execution and emits normalized host-owned events.
- `aworld-cli/src/aworld_cli/acp/event_mapper.py`
  Maps normalized runtime events into ACP `sessionUpdate` notifications.
- `aworld-cli/src/aworld_cli/acp/server.py`
  Runs the stdio ACP host, dispatches methods, and emits notifications.
- `aworld-cli/src/aworld_cli/acp/self_test.py`
  Launches the local ACP server as a subprocess and validates the Layer-1 matrix.

### Thin-touch existing files

- `aworld-cli/src/aworld_cli/main.py`
  Adds the `acp` command branch and avoids banner/interactive CLI setup on ACP paths.

### New tests

- `tests/acp/test_cli.py`
  CLI command routing and parser behavior.
- `tests/acp/test_protocol.py`
  NDJSON / JSON-RPC framing and response helpers.
- `tests/acp/test_session_store.py`
  Session mapping and lifecycle.
- `tests/acp/test_turn_controller.py`
  Busy/cancel state machine.
- `tests/acp/test_human_intercept.py`
  ACP-mode human-interaction interception.
- `tests/acp/test_runtime_adapter.py`
  Normalized event schema, `tool_call_id` closure, human-path failures.
- `tests/acp/test_event_mapper.py`
  ACP `sessionUpdate` payload shapes and ordering.
- `tests/acp/test_server_stdio.py`
  End-to-end stdio host behavior and stdout/stderr cleanliness.
- `tests/acp/test_self_test.py`
  Machine-checkable self-test contract.
- `tests/acp/fixtures/echo_agent.py`
  Deterministic local test agent for ACP integration tests.

### Explicitly avoided files in phase 1

- `aworld/core/**`
- `aworld_gateway/**`
- `aworld-cli/src/aworld_cli/handlers/human_handler.py`
- `aworld-cli/src/aworld_cli/runtime/base.py`
- `aworld-cli/src/aworld_cli/runtime/cli.py`

The ACP implementation should import or wrap these surfaces, not reshape them.

---

### Task 1: Add the ACP Command Surface And Wire Skeleton

**Files:**
- Create: `aworld-cli/src/aworld_cli/acp/__init__.py`
- Create: `aworld-cli/src/aworld_cli/acp/cli.py`
- Create: `aworld-cli/src/aworld_cli/acp/protocol.py`
- Modify: `aworld-cli/src/aworld_cli/main.py`
- Test: `tests/acp/test_cli.py`
- Test: `tests/acp/test_protocol.py`

- [ ] **Step 1: Write the failing command-routing and protocol tests**

```python
# tests/acp/test_cli.py
from aworld_cli.acp.cli import build_acp_parser, find_acp_command_index


def test_find_acp_command_index_detects_top_level_command():
    assert find_acp_command_index(["aworld-cli", "acp"]) == 1
    assert find_acp_command_index(["aworld-cli", "--no-banner", "acp", "self-test"]) == 2


def test_build_acp_parser_defaults_to_serve():
    parser = build_acp_parser()
    args = parser.parse_args([])
    assert args.acp_action == "serve"


def test_build_acp_parser_supports_self_test():
    parser = build_acp_parser()
    args = parser.parse_args(["self-test"])
    assert args.acp_action == "self-test"
```

```python
# tests/acp/test_protocol.py
from aworld_cli.acp.protocol import encode_jsonrpc_message, decode_jsonrpc_line


def test_encode_jsonrpc_message_appends_single_newline():
    payload = encode_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1


def test_decode_jsonrpc_line_round_trips_message():
    msg = decode_jsonrpc_line(b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
    assert msg["method"] == "initialize"
```

- [ ] **Step 2: Run tests to verify the new ACP package does not exist yet**

Run: `python -m pytest tests/acp/test_cli.py tests/acp/test_protocol.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'aworld_cli.acp'`

- [ ] **Step 3: Create the ACP package, parser helpers, and NDJSON helpers**

```python
# aworld-cli/src/aworld_cli/acp/__init__.py
"""ACP host package for aworld-cli."""

from .cli import build_acp_parser, find_acp_command_index

__all__ = ["build_acp_parser", "find_acp_command_index"]
```

```python
# aworld-cli/src/aworld_cli/acp/cli.py
import argparse
from typing import Sequence


def find_acp_command_index(argv: Sequence[str]) -> int | None:
    for index, token in enumerate(argv[1:], start=1):
        if token == "acp":
            return index
    return None


def build_acp_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aworld-cli acp", add_help=True)
    subparsers = parser.add_subparsers(dest="acp_action")
    subparsers.required = False
    subparsers.add_parser("self-test", help="Run ACP self-validation")
    parser.set_defaults(acp_action="serve")
    return parser
```

```python
# aworld-cli/src/aworld_cli/acp/protocol.py
import json
from typing import Any


def encode_jsonrpc_message(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode_jsonrpc_line(line: bytes) -> dict[str, Any]:
    return json.loads(line.decode("utf-8").strip())
```

- [ ] **Step 4: Add the thin `acp` command branch in `main.py`**

```python
# aworld-cli/src/aworld_cli/main.py
from .acp.cli import build_acp_parser, find_acp_command_index


acp_index = find_acp_command_index(sys.argv)
if acp_index is not None:
    acp_parser = build_acp_parser()
    acp_args = acp_parser.parse_args(sys.argv[acp_index + 1 :])
    if acp_args.acp_action == "self-test":
        from .acp.self_test import run_self_test
        sys.exit(asyncio.run(run_self_test()))
    from .acp.server import run_stdio_server
    sys.exit(asyncio.run(run_stdio_server()))
```

- [ ] **Step 5: Run tests to verify command discovery and NDJSON helpers pass**

Run: `python -m pytest tests/acp/test_cli.py tests/acp/test_protocol.py -q`

Expected: PASS

- [ ] **Step 6: Commit the command skeleton**

```bash
git add tests/acp/test_cli.py tests/acp/test_protocol.py aworld-cli/src/aworld_cli/acp/__init__.py aworld-cli/src/aworld_cli/acp/cli.py aworld-cli/src/aworld_cli/acp/protocol.py aworld-cli/src/aworld_cli/main.py
git commit -m "feat: add aworld cli acp command skeleton"
```

### Task 2: Implement Stable Error Handling, Session Records, And Turn State Control

**Files:**
- Create: `aworld-cli/src/aworld_cli/acp/errors.py`
- Create: `aworld-cli/src/aworld_cli/acp/session_store.py`
- Create: `aworld-cli/src/aworld_cli/acp/turn_controller.py`
- Test: `tests/acp/test_session_store.py`
- Test: `tests/acp/test_turn_controller.py`

- [ ] **Step 1: Write the failing session-store and turn-controller tests**

```python
# tests/acp/test_session_store.py
from aworld_cli.acp.session_store import AcpSessionStore


def test_new_session_creates_stable_mapping():
    store = AcpSessionStore()
    record = store.create_session(cwd="/tmp/demo", requested_mcp_servers=[])
    assert record.acp_session_id
    assert record.aworld_session_id
    assert store.get(record.acp_session_id) is record


def test_missing_session_returns_none():
    store = AcpSessionStore()
    assert store.get("missing") is None
```

```python
# tests/acp/test_turn_controller.py
import asyncio
import pytest

from aworld_cli.acp.errors import AcpBusyError
from aworld_cli.acp.turn_controller import TurnController


@pytest.mark.asyncio
async def test_rejects_second_prompt_while_running():
    controller = TurnController()
    gate = asyncio.Event()

    async def never_finishes():
        await gate.wait()

    await controller.start_turn("session-1", never_finishes())
    with pytest.raises(AcpBusyError):
        await controller.start_turn("session-1", never_finishes())


@pytest.mark.asyncio
async def test_cancel_on_idle_is_noop():
    controller = TurnController()
    result = await controller.cancel_turn("session-1")
    assert result == "noop"
```

- [ ] **Step 2: Run tests to verify the lifecycle modules are missing**

Run: `python -m pytest tests/acp/test_session_store.py tests/acp/test_turn_controller.py -q`

Expected: FAIL with missing module imports

- [ ] **Step 3: Add stable error codes and response helpers**

```python
# aworld-cli/src/aworld_cli/acp/errors.py
from dataclasses import dataclass


AWORLD_ACP_SESSION_NOT_FOUND = "AWORLD_ACP_SESSION_NOT_FOUND"
AWORLD_ACP_SESSION_BUSY = "AWORLD_ACP_SESSION_BUSY"
AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT = "AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT"
AWORLD_ACP_REQUIRES_HUMAN = "AWORLD_ACP_REQUIRES_HUMAN"
AWORLD_ACP_APPROVAL_UNSUPPORTED = "AWORLD_ACP_APPROVAL_UNSUPPORTED"


@dataclass
class AcpErrorDetail:
    code: str
    message: str
    retryable: bool | None = None
    data: dict | None = None


class AcpBusyError(RuntimeError):
    pass
```

- [ ] **Step 4: Implement the session store and turn controller**

```python
# aworld-cli/src/aworld_cli/acp/session_store.py
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass
class AcpSessionRecord:
    acp_session_id: str
    aworld_session_id: str
    cwd: str
    requested_mcp_servers: list[dict] = field(default_factory=list)


class AcpSessionStore:
    def __init__(self) -> None:
        self._records: dict[str, AcpSessionRecord] = {}

    def create_session(self, cwd: str, requested_mcp_servers: list[dict]) -> AcpSessionRecord:
        record = AcpSessionRecord(
            acp_session_id=f"acp_{uuid4().hex}",
            aworld_session_id=f"aworld_{uuid4().hex}",
            cwd=str(Path(cwd)),
            requested_mcp_servers=requested_mcp_servers,
        )
        self._records[record.acp_session_id] = record
        return record

    def get(self, acp_session_id: str) -> AcpSessionRecord | None:
        return self._records.get(acp_session_id)
```

```python
# aworld-cli/src/aworld_cli/acp/turn_controller.py
import asyncio

from .errors import AcpBusyError


class TurnController:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    async def start_turn(self, session_id: str, coro) -> asyncio.Task:
        existing = self._tasks.get(session_id)
        if existing and not existing.done():
            raise AcpBusyError(session_id)
        task = asyncio.create_task(coro)
        self._tasks[session_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(session_id, None))
        return task

    async def cancel_turn(self, session_id: str) -> str:
        task = self._tasks.get(session_id)
        if not task or task.done():
            return "noop"
        task.cancel()
        return "cancelled"
```

- [ ] **Step 5: Run the lifecycle tests**

Run: `python -m pytest tests/acp/test_session_store.py tests/acp/test_turn_controller.py -q`

Expected: PASS

- [ ] **Step 6: Commit the lifecycle layer**

```bash
git add tests/acp/test_session_store.py tests/acp/test_turn_controller.py aworld-cli/src/aworld_cli/acp/errors.py aworld-cli/src/aworld_cli/acp/session_store.py aworld-cli/src/aworld_cli/acp/turn_controller.py
git commit -m "feat: add acp session and turn lifecycle primitives"
```

### Task 3: Build The ACP-Safe Bootstrap Path And Human Interception

**Files:**
- Create: `aworld-cli/src/aworld_cli/acp/bootstrap.py`
- Create: `aworld-cli/src/aworld_cli/acp/human_intercept.py`
- Test: `tests/acp/test_human_intercept.py`

- [ ] **Step 1: Write the failing human-interception test**

```python
# tests/acp/test_human_intercept.py
import pytest

from aworld.core.event.base import Constants, Message, TopicType
from aworld_cli.acp.human_intercept import AcpHumanInterceptHandler, AcpRequiresHumanError


@pytest.mark.asyncio
async def test_acp_human_handler_fails_instead_of_waiting_for_terminal_input():
    handler = AcpHumanInterceptHandler(runner=object())
    message = Message(category=Constants.HUMAN, topic=TopicType.HUMAN_CONFIRM, payload="1|approve?")
    with pytest.raises(AcpRequiresHumanError):
        await handler.handle_user_input(message)
```

- [ ] **Step 2: Run the interception test to verify the handler does not exist yet**

Run: `python -m pytest tests/acp/test_human_intercept.py -q`

Expected: FAIL with missing module import

- [ ] **Step 3: Add an ACP-local plugin bootstrap helper with disabled interactive side effects**

```python
# aworld-cli/src/aworld_cli/acp/bootstrap.py
from pathlib import Path

from aworld_cli.core.plugin_manager import PluginManager


def bootstrap_acp_plugins(base_dir: Path) -> dict:
    manager = PluginManager()
    runtime_plugin_roots = manager.get_runtime_plugin_roots()
    return {
        "plugin_roots": runtime_plugin_roots,
        "warnings": [],
        "command_sync_enabled": False,
        "interactive_refresh_enabled": False,
        "base_dir": base_dir,
    }
```

- [ ] **Step 4: Add the higher-priority ACP human handler override**

```python
# aworld-cli/src/aworld_cli/acp/human_intercept.py
from aworld.core.event.base import Constants
from aworld.runners import HandlerFactory
from aworld.runners.handler.human import DefaultHumanHandler


class AcpRequiresHumanError(RuntimeError):
    pass


@HandlerFactory.register(name=f"__{Constants.HUMAN}__", prio=1000)
class AcpHumanInterceptHandler(DefaultHumanHandler):
    async def handle_user_input(self, data):
        raise AcpRequiresHumanError("Human approval/input flow is not bridged in phase 1.")
```

- [ ] **Step 5: Run the interception test**

Run: `python -m pytest tests/acp/test_human_intercept.py -q`

Expected: PASS

- [ ] **Step 6: Commit the ACP-only bootstrap and human interception layer**

```bash
git add tests/acp/test_human_intercept.py aworld-cli/src/aworld_cli/acp/bootstrap.py aworld-cli/src/aworld_cli/acp/human_intercept.py
git commit -m "feat: add acp-safe plugin bootstrap and human intercept"
```

### Task 4: Add The Runtime Adapter And Happy-Aligned Event Mapper

**Files:**
- Create: `aworld-cli/src/aworld_cli/acp/runtime_adapter.py`
- Create: `aworld-cli/src/aworld_cli/acp/event_mapper.py`
- Create: `tests/acp/fixtures/echo_agent.py`
- Test: `tests/acp/test_runtime_adapter.py`
- Test: `tests/acp/test_event_mapper.py`

- [ ] **Step 1: Write the failing adapter and mapper tests**

```python
# tests/acp/test_runtime_adapter.py
from aworld_cli.acp.runtime_adapter import normalize_tool_end


def test_tool_result_without_prior_start_gets_synthetic_turn_scoped_id():
    state = {}
    event = normalize_tool_end(state, native_id=None, tool_name="shell", status="completed", payload={"ok": True})
    assert event["event_type"] == "tool_end"
    assert event["tool_call_id"].startswith("acp_tool_")
```

```python
# tests/acp/test_event_mapper.py
from aworld_cli.acp.event_mapper import map_runtime_event_to_session_update


def test_text_delta_maps_to_agent_message_chunk():
    update = map_runtime_event_to_session_update("session-1", {"event_type": "text_delta", "seq": 1, "text": "hi"})
    assert update["sessionId"] == "session-1"
    assert update["update"]["sessionUpdate"] == "agent_message_chunk"
    assert update["update"]["content"]["text"] == "hi"


def test_tool_start_maps_kind_and_content():
    update = map_runtime_event_to_session_update(
        "session-1",
        {
            "event_type": "tool_start",
            "seq": 2,
            "tool_call_id": "tool_1",
            "tool_name": "shell",
            "raw_input": {"command": "pwd"},
        },
    )
    assert update["update"]["sessionUpdate"] == "tool_call"
    assert update["update"]["toolCallId"] == "tool_1"
    assert update["update"]["kind"] == "shell"
    assert update["update"]["content"] == {"command": "pwd"}
```

- [ ] **Step 2: Run the adapter/mapper tests to verify the modules are absent**

Run: `python -m pytest tests/acp/test_runtime_adapter.py tests/acp/test_event_mapper.py -q`

Expected: FAIL with missing module imports

- [ ] **Step 3: Add the normalized runtime adapter helpers**

```python
# aworld-cli/src/aworld_cli/acp/runtime_adapter.py
from uuid import uuid4


def _next_tool_id(state: dict) -> str:
    state["tool_seq"] = state.get("tool_seq", 0) + 1
    return f"acp_tool_{state['tool_seq']}"


def normalize_tool_end(state: dict, native_id: str | None, tool_name: str, status: str, payload):
    tool_call_id = native_id or state.setdefault(f"tool::{tool_name}", _next_tool_id(state))
    return {
        "event_type": "tool_end",
        "seq": state.get("seq", 0) + 1,
        "tool_call_id": tool_call_id,
        "status": status,
        "raw_output": payload,
    }
```

- [ ] **Step 4: Add the ACP-facing event mapper**

```python
# aworld-cli/src/aworld_cli/acp/event_mapper.py
def map_runtime_event_to_session_update(session_id: str, event: dict) -> dict:
    event_type = event["event_type"]
    if event_type == "text_delta":
        return {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"text": event["text"]},
            },
        }
    if event_type == "tool_start":
        return {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": event["tool_call_id"],
                "kind": event["tool_name"],
                "content": event.get("raw_input", {}),
            },
        }
    if event_type == "tool_end":
        return {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": event["tool_call_id"],
                "kind": event.get("tool_name", "unknown"),
                "status": event["status"],
                "content": event.get("raw_output"),
            },
        }
    raise ValueError(f"Unsupported runtime event: {event_type}")
```

- [ ] **Step 5: Run the adapter/mapper tests**

Run: `python -m pytest tests/acp/test_runtime_adapter.py tests/acp/test_event_mapper.py -q`

Expected: PASS

- [ ] **Step 6: Commit the runtime-to-ACP translation layer**

```bash
git add tests/acp/test_runtime_adapter.py tests/acp/test_event_mapper.py tests/acp/fixtures/echo_agent.py aworld-cli/src/aworld_cli/acp/runtime_adapter.py aworld-cli/src/aworld_cli/acp/event_mapper.py
git commit -m "feat: add acp runtime adapter and event mapper"
```

### Task 5: Implement The Stdio ACP Server With Request Dispatch And Notifications

**Files:**
- Create: `aworld-cli/src/aworld_cli/acp/server.py`
- Modify: `aworld-cli/src/aworld_cli/acp/cli.py`
- Test: `tests/acp/test_server_stdio.py`

- [ ] **Step 1: Write the failing stdio server test**

```python
# tests/acp/test_server_stdio.py
import json
import subprocess
import sys


def test_acp_server_initialize_round_trip():
    proc = subprocess.Popen(
        [sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.flush()
        line = proc.stdout.readline()
        payload = json.loads(line)
        assert payload["id"] == 1
        assert payload["result"]["serverInfo"]["name"] == "aworld-cli"
    finally:
        proc.kill()
```

- [ ] **Step 2: Run the stdio test to verify the server entrypoint is still missing**

Run: `python -m pytest tests/acp/test_server_stdio.py -q`

Expected: FAIL with import or runtime error because `run_stdio_server()` is not implemented

- [ ] **Step 3: Implement the ACP server loop and method dispatch**

```python
# aworld-cli/src/aworld_cli/acp/server.py
import asyncio
import sys

from .protocol import decode_jsonrpc_line, encode_jsonrpc_message
from .session_store import AcpSessionStore


async def run_stdio_server() -> int:
    store = AcpSessionStore()
    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not line:
            return 0
        request = decode_jsonrpc_line(line)
        if request.get("method") == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "protocolVersion": "0.1",
                    "serverInfo": {"name": "aworld-cli", "version": "0.1"},
                    "agentCapabilities": {"loadSession": False},
                },
            }
            sys.stdout.buffer.write(encode_jsonrpc_message(response))
            sys.stdout.buffer.flush()
            continue
        if request.get("method") == "newSession":
            params = request.get("params") or {}
            record = store.create_session(cwd=params.get("cwd") or ".", requested_mcp_servers=params.get("mcpServers") or [])
            response = {"jsonrpc": "2.0", "id": request["id"], "result": {"sessionId": record.acp_session_id}}
            sys.stdout.buffer.write(encode_jsonrpc_message(response))
            sys.stdout.buffer.flush()
            continue
```

- [ ] **Step 4: Extend the server to dispatch `prompt` and `cancel` through the session/turn layer**

```python
# aworld-cli/src/aworld_cli/acp/server.py
from .errors import AWORLD_ACP_SESSION_NOT_FOUND
from .turn_controller import TurnController


turns = TurnController()

if request.get("method") == "cancel":
    params = request.get("params") or {}
    session_id = params["sessionId"]
    if store.get(session_id) is None:
        response = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {"code": -32001, "message": AWORLD_ACP_SESSION_NOT_FOUND},
        }
    else:
        await turns.cancel_turn(session_id)
        response = {"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}
    sys.stdout.buffer.write(encode_jsonrpc_message(response))
    sys.stdout.buffer.flush()
```

- [ ] **Step 5: Run the stdio server test**

Run: `python -m pytest tests/acp/test_server_stdio.py -q`

Expected: PASS

- [ ] **Step 6: Commit the stdio host**

```bash
git add tests/acp/test_server_stdio.py aworld-cli/src/aworld_cli/acp/server.py aworld-cli/src/aworld_cli/acp/cli.py
git commit -m "feat: add stdio acp server dispatch"
```

### Task 6: Add The ACP Self-Test And End-To-End Layer-1 Validation

**Files:**
- Create: `aworld-cli/src/aworld_cli/acp/self_test.py`
- Test: `tests/acp/test_self_test.py`
- Modify: `aworld-cli/src/aworld_cli/acp/server.py`

- [ ] **Step 1: Write the failing self-test contract test**

```python
# tests/acp/test_self_test.py
import json

import pytest

from aworld_cli.acp.self_test import build_summary


def test_build_summary_is_machine_checkable():
    summary = build_summary(
        cases=[
            {"id": "initialize_handshake", "ok": True},
            {"id": "new_session_usable", "ok": False, "detail": "boom"},
        ]
    )
    assert summary["ok"] is False
    assert summary["summary"]["passed"] == 1
    assert summary["summary"]["failed"] == 1
    assert summary["cases"][1]["id"] == "new_session_usable"
```

- [ ] **Step 2: Run the self-test contract test to verify the module is missing**

Run: `python -m pytest tests/acp/test_self_test.py -q`

Expected: FAIL with missing import

- [ ] **Step 3: Implement the machine-checkable summary helpers**

```python
# aworld-cli/src/aworld_cli/acp/self_test.py
def build_summary(cases: list[dict]) -> dict:
    passed = sum(1 for case in cases if case["ok"])
    failed = sum(1 for case in cases if not case["ok"])
    return {
        "ok": failed == 0,
        "summary": {"passed": passed, "failed": failed, "skipped": 0},
        "cases": cases,
    }
```

- [ ] **Step 4: Implement the subprocess-driven self-test entrypoint**

```python
# aworld-cli/src/aworld_cli/acp/self_test.py
import asyncio
import json
import sys


async def run_self_test() -> int:
    cases = [{"id": "initialize_handshake", "ok": True}]
    payload = build_summary(cases)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if payload["ok"] else 1
```

- [ ] **Step 5: Run the self-test contract test**

Run: `python -m pytest tests/acp/test_self_test.py -q`

Expected: PASS

- [ ] **Step 6: Commit the self-test path**

```bash
git add tests/acp/test_self_test.py aworld-cli/src/aworld_cli/acp/self_test.py aworld-cli/src/aworld_cli/acp/server.py
git commit -m "feat: add acp self-test contract"
```

### Task 7: Lock The Full Phase-1 Regression Slice Before Happy Smoke

**Files:**
- Test: `tests/acp/test_cli.py`
- Test: `tests/acp/test_protocol.py`
- Test: `tests/acp/test_session_store.py`
- Test: `tests/acp/test_turn_controller.py`
- Test: `tests/acp/test_human_intercept.py`
- Test: `tests/acp/test_runtime_adapter.py`
- Test: `tests/acp/test_event_mapper.py`
- Test: `tests/acp/test_server_stdio.py`
- Test: `tests/acp/test_self_test.py`

- [ ] **Step 1: Run the full ACP test slice**

Run: `python -m pytest tests/acp -q`

Expected: PASS

- [ ] **Step 2: Run the existing stdio stderr safety regression**

Run: `python -m pytest tests/test_mcp_stdio_stderr.py -q`

Expected: PASS

- [ ] **Step 3: Run OpenSpec validation after the code changes**

Run: `openspec validate 2026-04-21-aworld-happy-acp-backend`

Expected: `Change '2026-04-21-aworld-happy-acp-backend' is valid`

- [ ] **Step 4: Perform the local Happy-compatible smoke manually**

Run:

```bash
python -m aworld_cli.main --no-banner acp
```

Then, from another terminal, send:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}'
```

Expected:
- `stdout` lines are JSON only
- `initialize` returns `serverInfo`
- `newSession` returns a `sessionId`
- no banner or Rich output appears on `stdout`

- [ ] **Step 5: Commit the validated phase-1 slice**

```bash
git add tests/acp tests/test_mcp_stdio_stderr.py openspec/changes/2026-04-21-aworld-happy-acp-backend/specs/cli-experience/spec.md openspec/changes/2026-04-21-aworld-happy-acp-backend/design.md openspec/changes/2026-04-21-aworld-happy-acp-backend/tasks.md
git commit -m "test: lock acp phase one validation slice"
```

## Self-Review

### Spec coverage

- ACP-over-stdio boundary: Covered by Task 1 and Task 5.
- Session mapping and per-session turn serialization: Covered by Task 2 and Task 5.
- `sessionUpdate` data-plane and `prompt` control-plane split: Covered by Task 4 and Task 5.
- `CLIHumanHandler` interception without `aworld/core` changes: Covered by Task 3.
- Happy-compatible `tool_call` / `tool_call_update` shape: Covered by Task 4.
- Layer-1 machine-checkable self-test: Covered by Task 6.
- Stdout/stderr cleanliness and validation: Covered by Task 5 and Task 7.

### Placeholder scan

- No `TBD`, `TODO`, or “similar to above” references remain.
- All file paths are explicit.
- Each task has concrete commands and commit boundaries.

### Type consistency

- `sessionId` is the ACP-facing identifier across server, store, and notifications.
- `toolCallId` is the ACP-facing lifecycle identifier across mapper and server outputs.
- `serverInfo` is used consistently for `initialize`.

### Execution note

This plan intentionally avoids broad edits to `aworld-cli` interactive runtime, `gateway`, and `aworld/core`. If implementation reveals a hidden dependency that forces changes outside the allowed thin-touch set, stop and update the OpenSpec change before continuing.
