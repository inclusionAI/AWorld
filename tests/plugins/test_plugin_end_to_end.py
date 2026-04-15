from pathlib import Path

from aworld_cli.core.command_system import CommandRegistry
from aworld_cli.core.plugin_manager import PluginManager
from aworld_cli.plugin_framework.commands import register_plugin_commands
from aworld_cli.plugin_framework.discovery import discover_plugins
from aworld_cli.runtime.base import BaseCliRuntime


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
