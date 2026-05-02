from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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


@pytest.fixture(autouse=True)
def _restore_command_registry():
    snapshot = CommandRegistry.snapshot()
    try:
        yield
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_bridge_executes_builtin_tool_command_from_fresh_registry(tmp_path: Path) -> None:
    from aworld_cli.core.command_bridge import CommandBridge

    CommandRegistry.clear()
    bridge = CommandBridge(plugin_roots=[])

    result = await bridge.execute(
        text="/help",
        cwd=str(tmp_path),
        session_id="session-1",
    )

    assert result.handled is True
    assert result.command_name == "help"
    assert result.status == "completed"
    assert "Available commands:" in result.text


@pytest.mark.asyncio
async def test_bridge_executes_builtin_cron_tool_command(tmp_path: Path) -> None:
    from aworld_cli.core.command_bridge import CommandBridge

    CommandRegistry.clear()
    bridge = CommandBridge(plugin_roots=[])

    result = await bridge.execute(
        text="/cron status",
        cwd=str(tmp_path),
        session_id="session-1",
    )

    assert result.handled is True
    assert result.command_name == "cron"
    assert result.status == "completed"
    assert "Cron 调度器状态" in result.text


@pytest.mark.asyncio
async def test_bridge_executes_plugin_tool_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aworld_cli.core.command_bridge import CommandBridge

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    CommandRegistry.clear()
    bridge = CommandBridge(plugin_roots=[_memory_plugin_root()])

    result = await bridge.execute(
        text="/memory reload",
        cwd=str(workspace),
        session_id="session-1",
    )

    assert result.handled is True
    assert result.command_name == "memory"
    assert result.status == "completed"
    assert "read from disk on demand" in result.text


@pytest.mark.asyncio
async def test_bridge_executes_plugin_remember_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aworld_cli.core.command_bridge import CommandBridge

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    CommandRegistry.clear()
    bridge = CommandBridge(plugin_roots=[_memory_plugin_root()])

    result = await bridge.execute(
        text="/remember always use pnpm",
        cwd=str(workspace),
        session_id="session-1",
    )

    assert result.handled is True
    assert result.command_name == "remember"
    assert result.status == "completed"
    assert "Saved durable memory" in result.text


@pytest.mark.asyncio
async def test_bridge_executes_builtin_tasks_tool_command(tmp_path: Path) -> None:
    from aworld_cli.core.command_bridge import CommandBridge

    class _FakeTaskManager:
        def list_tasks(self):
            return []

        def get_stats(self):
            return {
                "running": 0,
                "completed": 0,
                "failed": 0,
                "timeout": 0,
                "interrupted": 0,
                "pending": 0,
                "cancelled": 0,
            }

    class _FakeRuntime:
        background_task_manager = _FakeTaskManager()

    CommandRegistry.clear()
    bridge = CommandBridge(plugin_roots=[])

    result = await bridge.execute(
        text="/tasks list",
        cwd=str(tmp_path),
        session_id="session-1",
        runtime=_FakeRuntime(),
    )

    assert result.handled is True
    assert result.command_name == "tasks"
    assert result.status == "completed"
    assert "No background tasks found" in result.text


@pytest.mark.asyncio
async def test_bridge_executes_builtin_plugins_tool_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.core.command_bridge import CommandBridge
    from aworld_cli.commands.plugins_cmd import PluginsCommand

    class FakePluginManager:
        def __init__(self):
            self.plugin_dir = Path("/tmp/plugins")

    monkeypatch.setattr("aworld_cli.commands.plugins_cmd.PluginManager", FakePluginManager)
    monkeypatch.setattr(
        "aworld_cli.commands.plugins_cmd.list_available_plugins",
        lambda _manager: [
            {
                "name": "aworld-hud",
                "plugin_id": "aworld-hud",
                "enabled": True,
                "lifecycle_phase": "activate",
                "framework_source": "manifest",
                "capabilities": ["hud"],
                "source": "built-in",
                "has_agents": False,
                "has_skills": False,
                "path": "/repo/aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud",
            }
        ],
    )

    CommandRegistry.clear()
    CommandRegistry.register(PluginsCommand())
    bridge = CommandBridge(plugin_roots=[])

    result = await bridge.execute(
        text="/plugins list",
        cwd=str(tmp_path),
        session_id="session-1",
    )

    assert result.handled is True
    assert result.command_name == "plugins"
    assert result.status == "completed"
    assert "Available plugins (1)" in result.text


@pytest.mark.asyncio
async def test_bridge_rejects_prompt_commands_in_phase_one(tmp_path: Path) -> None:
    from aworld_cli.core.command_bridge import CommandBridge

    CommandRegistry.clear()
    bridge = CommandBridge(plugin_roots=[])

    result = await bridge.execute(
        text="/review",
        cwd=str(tmp_path),
        session_id="session-1",
    )

    assert result.handled is True
    assert result.command_name == "review"
    assert result.status == "unsupported"
    assert "not yet supported" in result.text.lower()


@pytest.mark.asyncio
async def test_bridge_executes_prompt_command_via_prompt_executor() -> None:
    from aworld_cli.core.command_bridge import CommandBridge

    repo_root = Path(__file__).resolve().parents[2]
    CommandRegistry.clear()
    bridge = CommandBridge(plugin_roots=[])
    seen: dict[str, object] = {}

    async def fake_prompt_executor(*, prompt: str, allowed_tools, on_output=None) -> str:
        seen["prompt"] = prompt
        seen["allowed_tools"] = allowed_tools
        seen["on_output"] = on_output
        return "prompt-result"

    result = await bridge.execute(
        text="/diff main",
        cwd=str(repo_root),
        session_id="session-1",
        prompt_executor=fake_prompt_executor,
    )

    assert result.handled is True
    assert result.command_name == "diff"
    assert result.status == "completed"
    assert result.text == "prompt-result"
    assert "Diff Summary Task" in str(seen["prompt"])
    assert "main" in str(seen["prompt"])
    assert "git_diff" in list(seen["allowed_tools"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_text", "command_name", "expected_tool"),
    [
        ("/review", "review", "git_diff"),
        ("/commit", "commit", "git_commit"),
    ],
)
async def test_bridge_executes_builtin_prompt_commands_via_prompt_executor(
    tmp_path: Path,
    command_text: str,
    command_name: str,
    expected_tool: str,
) -> None:
    from aworld_cli.core.command_bridge import CommandBridge

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    CommandRegistry.clear()
    bridge = CommandBridge(plugin_roots=[])
    seen: dict[str, object] = {}

    async def fake_prompt_executor(*, prompt: str, allowed_tools, on_output=None) -> str:
        seen["prompt"] = prompt
        seen["allowed_tools"] = allowed_tools
        seen["on_output"] = on_output
        return f"{command_name}-result"

    result = await bridge.execute(
        text=command_text,
        cwd=str(tmp_path),
        session_id="session-1",
        prompt_executor=fake_prompt_executor,
    )

    assert result.handled is True
    assert result.command_name == command_name
    assert result.status == "completed"
    assert result.text == f"{command_name}-result"
    assert expected_tool in list(seen["allowed_tools"])
