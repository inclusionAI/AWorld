from types import SimpleNamespace

from pathlib import Path

from aworld_cli.core.command_system import CommandContext, CommandRegistry
from aworld_cli.plugin_framework.commands import PluginPromptCommand, register_plugin_commands, sync_plugin_commands
from aworld_cli.plugin_framework.discovery import discover_plugins
from aworld_cli.plugin_framework.state import PluginStateStore
from aworld_cli.runtime.base import BaseCliRuntime


def test_register_plugin_command_from_manifest():
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("code-review")
        assert command is not None
        assert command.description == "Review the current pull request"
        assert "gh pr view" in command.allowed_tools[0]
    finally:
        CommandRegistry.restore(snapshot)


async def test_plugin_prompt_command_reads_packaged_prompt():
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("code-review")
        prompt = await command.get_prompt(CommandContext(cwd=str(plugin_root), user_args="--comment"))

        assert "Provide a code review for the given pull request." in prompt
        assert "--comment" in prompt
    finally:
        CommandRegistry.restore(snapshot)


def test_sync_plugin_commands_removes_stale_plugin_commands():
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        assert CommandRegistry.get("code-review") is not None

        sync_plugin_commands([])

        assert CommandRegistry.get("code-review") is None
    finally:
        CommandRegistry.restore(snapshot)


def test_plugin_command_workspace_state_is_shared_with_hook_runtime(tmp_path):
    plugin_root = tmp_path / "shared_plugin"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    (plugin_root / "commands").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        (
            "{"
            "\"id\": \"shared-plugin\", "
            "\"name\": \"shared-plugin\", "
            "\"version\": \"1.0.0\", "
            "\"entrypoints\": {"
            "\"commands\": ["
            "{"
            "\"id\": \"review-loop\", "
            "\"name\": \"review-loop\", "
            "\"target\": \"commands/review-loop.md\", "
            "\"scope\": \"workspace\""
            "}"
            "]"
            "}"
            "}"
        ),
        encoding="utf-8",
    )
    (plugin_root / "commands" / "review-loop.md").write_text("shared state", encoding="utf-8")

    plugin = discover_plugins([plugin_root])[0]
    entrypoint = plugin.manifest.entrypoints["commands"][0]
    command = PluginPromptCommand(plugin, entrypoint)

    class DummyRuntime(BaseCliRuntime):
        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://shared"

    runtime = DummyRuntime(agent_name="Aworld")
    runtime._plugin_state_store = PluginStateStore(tmp_path / "state")
    workspace_path = str(tmp_path / "workspace")

    state_path = command.resolve_state_path(
        CommandContext(cwd=workspace_path, user_args="", runtime=runtime)
    )
    assert state_path is not None
    state_path.write_text('{"iteration": 2}', encoding="utf-8")

    hook_state = runtime.build_plugin_hook_state(
        plugin_id="shared-plugin",
        scope="workspace",
        executor_instance=SimpleNamespace(
            context=SimpleNamespace(workspace_path=workspace_path, session_id="session-1")
        ),
    )

    assert hook_state["iteration"] == 2
