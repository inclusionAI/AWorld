import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld.plugins.discovery import discover_plugins
from aworld_cli.plugin_capabilities.hooks import PluginHookResult, load_plugin_hooks
from aworld_cli.plugin_capabilities.state import PluginStateStore
from aworld_cli.runtime.base import BaseCliRuntime


def test_load_plugin_hook_entrypoints():
    plugin_root = Path("tests/fixtures/plugins/ralph_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    hooks = load_plugin_hooks([plugin])

    assert "stop" in hooks
    assert hooks["stop"][0].entrypoint_id == "loop-stop"


@pytest.mark.asyncio
async def test_stop_hook_can_block_and_continue_session(tmp_path):
    plugin_root = Path("tests/fixtures/plugins/ralph_like").resolve()
    plugin = discover_plugins([plugin_root])[0]
    hooks = load_plugin_hooks([plugin])

    result = await hooks["stop"][0].run(
        event={"transcript_path": str(tmp_path / "transcript.jsonl")},
        state={"iteration": 1, "prompt": "keep going"},
    )

    assert result.action == "block_and_continue"
    assert result.follow_up_prompt == "keep going"


def test_plugin_hook_result_accepts_claude_style_block_decision():
    result = PluginHookResult.from_payload(
        {
            "decision": "block",
            "reason": "keep going",
            "systemMessage": "Loop continues",
        }
    )

    assert result.action == "block_and_continue"
    assert result.follow_up_prompt == "keep going"
    assert result.system_message == "Loop continues"


def test_plugin_hooks_are_sorted_by_priority_then_identity(tmp_path):
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"

    for plugin_root, plugin_id, priority in (
        (alpha, "alpha", 50),
        (beta, "beta", 10),
    ):
        (plugin_root / ".aworld-plugin").mkdir(parents=True)
        (plugin_root / "hooks").mkdir()
        (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
            (
                "{"
                f"\"id\": \"{plugin_id}\", "
                f"\"name\": \"{plugin_id}\", "
                "\"version\": \"1.0.0\", "
                "\"entrypoints\": {"
                "\"hooks\": ["
                "{"
                "\"id\": \"stop-loop\", "
                "\"target\": \"hooks/stop.py\", "
                "\"metadata\": {"
                "\"hook_point\": \"stop\", "
                f"\"priority\": {priority}"
                "}"
                "}"
                "]"
                "}"
                "}"
            ),
            encoding="utf-8",
        )
        (plugin_root / "hooks" / "stop.py").write_text(
            "def handle_event(event, state):\n    return {'action': 'allow'}\n",
            encoding="utf-8",
        )

    plugins = discover_plugins([alpha, beta])
    hooks = load_plugin_hooks(plugins)

    assert [hook.plugin_id for hook in hooks["stop"]] == ["beta", "alpha"]


class DummyRuntime(BaseCliRuntime):
    async def _load_agents(self):
        return []

    async def _create_executor(self, agent):
        return None

    def _get_source_type(self):
        return "TEST"

    def _get_source_location(self):
        return "test://runtime"


@pytest.mark.asyncio
async def test_runtime_plugin_hook_can_persist_plugin_state(tmp_path):
    plugin_root = tmp_path / "stateful"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    (plugin_root / "hooks").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        (
            "{"
            "\"id\": \"stateful\", "
            "\"name\": \"stateful\", "
            "\"version\": \"1.0.0\", "
            "\"entrypoints\": {"
            "\"hooks\": ["
            "{"
            "\"id\": \"task-started\", "
            "\"target\": \"hooks/task_started.py\", "
            "\"scope\": \"session\", "
            "\"metadata\": {\"hook_point\": \"task_started\"}"
            "}"
            "]"
            "}"
            "}"
        ),
        encoding="utf-8",
    )
    (plugin_root / "hooks" / "task_started.py").write_text(
        "def handle_event(event, state):\n"
        "    state['__plugin_state__'].update({'iteration': state.get('iteration', 0) + 1, 'task_id': event['task_id']})\n"
        "    return {'action': 'allow'}\n",
        encoding="utf-8",
    )

    plugin = discover_plugins([plugin_root])[0]
    runtime = DummyRuntime(agent_name="Aworld")
    runtime._plugin_hooks = load_plugin_hooks([plugin])
    runtime._plugin_state_store = PluginStateStore(tmp_path / "plugin-state")

    executor_instance = SimpleNamespace(
        session_id="session-1",
        context=SimpleNamespace(workspace_path=str(tmp_path), task_id="task-1"),
    )

    await runtime.run_plugin_hooks(
        "task_started",
        event={"task_id": "task-1"},
        executor_instance=executor_instance,
    )

    state = runtime.build_plugin_hook_state("stateful", "session", executor_instance=executor_instance)

    assert state["iteration"] == 1
    assert state["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_runtime_plugin_hooks_continue_after_timeout(tmp_path):
    slow_root = tmp_path / "slow"
    fast_root = tmp_path / "fast"

    for plugin_root, plugin_id, priority, timeout, source in (
        (
            slow_root,
            "slow",
            10,
            "0.01",
            "import asyncio\nasync def handle_event(event, state):\n    await asyncio.sleep(0.05)\n    return {'action': 'allow'}\n",
        ),
        (
            fast_root,
            "fast",
            20,
            None,
            "def handle_event(event, state):\n    return {'action': 'allow', 'reason': 'fast-path'}\n",
        ),
    ):
        (plugin_root / ".aworld-plugin").mkdir(parents=True)
        (plugin_root / "hooks").mkdir()
        metadata = f'"hook_point": "stop", "priority": {priority}'
        if timeout is not None:
            metadata = f'{metadata}, "timeout_seconds": {timeout}'
        (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
            (
                "{"
                f"\"id\": \"{plugin_id}\", "
                f"\"name\": \"{plugin_id}\", "
                "\"version\": \"1.0.0\", "
                "\"entrypoints\": {"
                "\"hooks\": ["
                "{"
                "\"id\": \"stop-hook\", "
                "\"target\": \"hooks/stop.py\", "
                "\"metadata\": {"
                f"{metadata}"
                "}"
                "}"
                "]"
                "}"
                "}"
            ),
            encoding="utf-8",
        )
        (plugin_root / "hooks" / "stop.py").write_text(source, encoding="utf-8")

    runtime = DummyRuntime(agent_name="Aworld")
    runtime._plugin_hooks = load_plugin_hooks(discover_plugins([slow_root, fast_root]))

    results = await runtime.run_plugin_hooks("stop", event={"task_id": "task-1"})

    assert [hook.plugin_id for hook, _ in results] == ["fast"]
    assert results[0][1].reason == "fast-path"
