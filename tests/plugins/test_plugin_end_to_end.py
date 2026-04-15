from pathlib import Path

from aworld_cli.core.command_system import CommandRegistry
from aworld_cli.core.plugin_manager import PluginManager
from aworld_cli.plugin_framework.commands import register_plugin_commands
from aworld_cli.plugin_framework.discovery import discover_plugins
from aworld_cli.runtime.base import BaseCliRuntime
from aworld_cli.runtime.cli import CliRuntime


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


def test_cli_runtime_scans_builtin_plugins_from_plugins_dir():
    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert any(path.parent.name == "plugins" and path.name == "smllc" for path in runtime.plugin_dirs)


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
