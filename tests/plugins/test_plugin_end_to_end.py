from pathlib import Path

from aworld_cli.core.command_system import CommandRegistry
from aworld_cli.core.plugin_manager import PluginManager, get_builtin_plugin_roots
from aworld.plugins.discovery import discover_plugins
from aworld_cli.console import AWorldCLI
from aworld_cli.plugin_capabilities.commands import register_plugin_commands
from aworld_cli.runtime.base import BaseCliRuntime
from aworld_cli.runtime.cli import CliRuntime


def _set_isolated_plugin_dir(monkeypatch, tmp_path: Path) -> Path:
    plugin_dir = tmp_path / ".aworld" / "plugins"
    monkeypatch.setattr("aworld_cli.core.plugin_manager.DEFAULT_PLUGIN_DIR", plugin_dir)
    return plugin_dir


def test_code_review_like_plugin_registers_command_and_assets(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()

    assert manager.install("code-review-like", local_path=str(plugin_root))

    discovered = discover_plugins(manager.get_plugin_roots())

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands(discovered)

        assert CommandRegistry.get("code-review") is not None
    finally:
        CommandRegistry.restore(snapshot)


def test_runtime_initialization_registers_enabled_plugin_commands(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()

    assert manager.install("code-review-like", local_path=str(plugin_root))

    class FakeRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            self.plugin_dirs = plugin_dirs
            super().__init__(agent_name="Aworld")

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self) -> str:
            return "TEST"

        def _get_source_location(self) -> str:
            return "test"

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        runtime = FakeRuntime(manager.get_plugin_roots())
        runtime._initialize_plugin_framework()

        assert CommandRegistry.get("code-review") is not None
    finally:
        CommandRegistry.restore(snapshot)


def test_builtin_plugin_namespace_moves_to_plugins_package():
    from aworld_cli.plugins.smllc.agents.aworld_agent import load_aworld_system_prompt as new_load
    from aworld_cli.inner_plugins.smllc.agents.aworld_agent import load_aworld_system_prompt as legacy_load

    assert new_load() == legacy_load()


def test_get_builtin_plugin_roots_prefers_builtin_plugins_for_aworld_hud():
    roots = get_builtin_plugin_roots()

    assert any(root.parent.name == "builtin_plugins" and root.name == "aworld_hud" for root in roots)


def test_cli_runtime_scans_builtin_plugins_from_plugins_dir(monkeypatch, tmp_path):
    _set_isolated_plugin_dir(monkeypatch, tmp_path)
    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert any(path.parent.name == "plugins" and path.name == "smllc" for path in runtime.plugin_dirs)
    assert any(path.parent.name == "builtin_plugins" and path.name == "aworld_hud" for path in runtime.plugin_dirs)


def test_cli_runtime_excludes_disabled_builtin_hud_plugin(monkeypatch, tmp_path):
    plugin_dir = _set_isolated_plugin_dir(monkeypatch, tmp_path)
    manager = PluginManager(plugin_dir=plugin_dir)

    manager.disable("aworld-hud")

    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert not any(path.name == "aworld_hud" for path in runtime.plugin_dirs)


def test_cli_runtime_includes_enabled_builtin_hud_plugin(monkeypatch, tmp_path):
    plugin_dir = _set_isolated_plugin_dir(monkeypatch, tmp_path)
    manager = PluginManager(plugin_dir=plugin_dir)

    manager.disable("aworld-hud")
    manager.enable("aworld-hud")

    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert any(path.name == "aworld_hud" for path in runtime.plugin_dirs)


def test_runtime_initializes_framework_registry_and_context_handlers():
    class DummyRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            super().__init__()
            self.plugin_dirs = plugin_dirs

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://plugins"

    runtime = DummyRuntime([Path("tests/fixtures/plugins/context_like").resolve()])
    runtime._initialize_plugin_framework()

    assert runtime._plugin_registry is not None
    assert runtime._plugin_registry.capabilities() == ("contexts",)
    assert [adapter.entrypoint_id for adapter in runtime.get_context_phase_handlers("schema")] == [
        "workspace-memory"
    ]


def test_runtime_tracks_active_plugins_by_capability(tmp_path):
    plugin_root = tmp_path / "runtime_capability"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "id": "runtime-capability",
          "name": "runtime-capability",
          "version": "1.0.0",
          "entrypoints": {
            "tools": [{"id": "tool", "target": "tools/tool.py"}],
            "runners": [{"id": "runner", "target": "runners/runner.py"}],
            "hud": [{"id": "hud", "target": "hud/status.py"}]
          }
        }
        ''',
        encoding="utf-8",
    )

    class DummyRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            super().__init__()
            self.plugin_dirs = plugin_dirs

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://plugins"

    runtime = DummyRuntime([plugin_root])
    runtime._initialize_plugin_framework()

    assert runtime.active_plugin_capabilities() == ("hud", "runners", "tools")
    assert [plugin.manifest.plugin_id for plugin in runtime.get_active_plugins("tools")] == ["runtime-capability"]
    assert [entry.entrypoint.entrypoint_id for entry in runtime.get_active_entrypoints("runners")] == ["runner"]


async def test_runtime_renders_third_party_hud_plugin_from_hook_driven_state(tmp_path):
    plugin_root = Path("tests/fixtures/plugins/hud_stateful_like").resolve()

    class DummyRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            super().__init__(agent_name="Aworld")
            self.plugin_dirs = plugin_dirs

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://hud-stateful-like"

    runtime = DummyRuntime([plugin_root])
    runtime._initialize_plugin_framework()
    runtime._plugin_state_store = type(runtime._plugin_state_store)(tmp_path / "plugin-state")

    executor_instance = type(
        "Executor",
        (),
        {
            "session_id": "session-1",
            "context": type("Context", (), {"workspace_path": str(tmp_path), "task_id": "task-1"})(),
        },
    )()

    await runtime.run_plugin_hooks(
        "task_started",
        event={"task_id": "task-1", "session_id": "session-1"},
        executor_instance=executor_instance,
    )
    runtime.update_hud_snapshot(
        session={"session_id": "session-1", "elapsed_seconds": 12.5},
        task={"current_task_id": "task-1", "status": "running"},
        usage={"input_tokens": 1200, "output_tokens": 300},
    )

    cli = AWorldCLI()
    text = cli._build_status_bar_text(runtime, agent_name="Aworld", mode="Chat", max_width=160)

    assert "PluginStatus: started" in text
    assert "Observed Task: task-1" in text
    assert "Usage: in 1.2k out 300" in text
    assert "Elapsed: 12.5s" in text
