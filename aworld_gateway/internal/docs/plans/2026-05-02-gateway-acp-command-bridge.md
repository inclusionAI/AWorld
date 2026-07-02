# Gateway / ACP Command Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared command bridge so `aworld-cli` slash commands, including plugin-backed workspace commands such as `/memory`, can run from gateway channels and ACP prompts without special-casing individual commands.

**Architecture:** Introduce a shared bootstrap-and-dispatch layer under `aworld_cli/core`, use it to load built-in and plugin-backed commands into `CommandRegistry`, then intercept slash inputs in gateway connectors and ACP before they reach the normal agent execution path.

**Tech Stack:** Python 3.10+, pytest, existing `CommandRegistry`, `PluginManager`, plugin discovery/activation, gateway connectors, ACP stdio server.

---

### Task 1: Add Shared Command Bridge Tests And Skeleton

**Files:**
- Create: `aworld-cli/src/aworld_cli/core/command_bridge.py`
- Create: `tests/core/test_command_bridge.py`

- [ ] **Step 1: Write the failing command bridge tests**

```python
import asyncio
from pathlib import Path

import pytest

from aworld_cli.core.command_bridge import CommandBridge
from aworld_cli.core.command_system import CommandRegistry


def _memory_plugin_root() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "aworld-cli"
        / "src"
        / "aworld_cli"
        / "builtin_plugins"
        / "memory_cli"
    )


@pytest.mark.asyncio
async def test_bridge_executes_builtin_tool_command(tmp_path):
    bridge = CommandBridge(plugin_roots=[])

    result = await bridge.execute(
        text="/help",
        cwd=str(tmp_path),
        session_id="session-1",
    )

    assert result.handled is True
    assert result.command_name == "help"
    assert "Available commands:" in result.text


@pytest.mark.asyncio
async def test_bridge_executes_plugin_tool_command(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    bridge = CommandBridge(plugin_roots=[_memory_plugin_root()])

    result = await bridge.execute(
        text="/memory reload",
        cwd=str(workspace),
        session_id="session-1",
    )

    assert result.handled is True
    assert result.command_name == "memory"
    assert "read from disk on demand" in result.text


@pytest.mark.asyncio
async def test_bridge_rejects_prompt_commands_in_phase_one(tmp_path):
    bridge = CommandBridge(plugin_roots=[])

    result = await bridge.execute(
        text="/review",
        cwd=str(tmp_path),
        session_id="session-1",
    )

    assert result.handled is True
    assert result.command_name == "review"
    assert "not yet supported" in result.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_command_bridge.py -q`
Expected: FAIL because `aworld_cli.core.command_bridge` does not exist yet.

- [ ] **Step 3: Write the minimal bridge implementation**

```python
from dataclasses import dataclass


@dataclass
class CommandBridgeResult:
    handled: bool
    command_name: str | None
    status: str
    text: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_command_bridge.py -q`
Expected: PASS

### Task 2: Integrate Bridge Into WeChat And DingTalk

**Files:**
- Modify: `aworld_gateway/channels/wechat/connector.py`
- Modify: `aworld_gateway/channels/dingding/connector.py`
- Modify: `tests/gateway/test_wechat_connector.py`
- Modify: `tests/gateway/test_dingding_connector.py`

- [ ] **Step 1: Write the failing channel interception tests**

```python
@pytest.mark.asyncio
async def test_wechat_slash_command_bypasses_router(...)

@pytest.mark.asyncio
async def test_dingding_slash_command_bypasses_agent_bridge(...)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_wechat_connector.py tests/gateway/test_dingding_connector.py -q`
Expected: FAIL because connectors still always route slash input through the agent path.

- [ ] **Step 3: Implement shared bridge interception**

```python
if self._command_bridge.is_slash_command(user_text):
    result = await self._command_bridge.execute(...)
    await self.send_text(..., text=result.text)
    return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_wechat_connector.py tests/gateway/test_dingding_connector.py -q`
Expected: PASS

### Task 3: Integrate Bridge Into ACP Prompt Handling

**Files:**
- Modify: `aworld-cli/src/aworld_cli/acp/server.py`
- Modify: `tests/acp/test_server_runtime_wiring.py`

- [ ] **Step 1: Write the failing ACP slash-command test**

```python
@pytest.mark.asyncio
async def test_prompt_executes_slash_command_without_invoking_output_bridge():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/acp/test_server_runtime_wiring.py -q`
Expected: FAIL because ACP always runs the agent output bridge for text prompts.

- [ ] **Step 3: Implement ACP interception**

```python
command_result = await self._command_bridge.execute(...)
if command_result.handled:
    await self._write_session_update_for_session(
        session_id,
        {
            "sessionUpdate": "agent_message_chunk",
            "content": {"text": command_result.text},
        },
    )
    return self._response(request_id, {"status": "completed"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/acp/test_server_runtime_wiring.py -q`
Expected: PASS

### Task 4: Run Focused Verification

**Files:**
- Modify: `docs/superpowers/specs/2026-05-02-gateway-acp-command-bridge-design.md`
- Modify: `docs/superpowers/plans/2026-05-02-gateway-acp-command-bridge.md`

- [ ] **Step 1: Run focused verification suites**

Run: `pytest tests/core/test_command_bridge.py tests/gateway/test_wechat_connector.py tests/gateway/test_dingding_connector.py tests/acp/test_server_runtime_wiring.py -q`
Expected: PASS

- [ ] **Step 2: Run regression spot checks**

Run: `pytest tests/plugins/test_plugin_commands.py tests/test_slash_commands.py tests/acp/test_server_stdio.py -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-02-gateway-acp-command-bridge-design.md docs/superpowers/plans/2026-05-02-gateway-acp-command-bridge.md aworld-cli/src/aworld_cli/core/command_bridge.py aworld_gateway/channels/wechat/connector.py aworld_gateway/channels/dingding/connector.py aworld-cli/src/aworld_cli/acp/server.py tests/core/test_command_bridge.py tests/gateway/test_wechat_connector.py tests/gateway/test_dingding_connector.py tests/acp/test_server_runtime_wiring.py
git commit -m "feat: bridge cli slash commands into gateway and acp"
```
