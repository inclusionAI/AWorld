from pathlib import Path

from aworld_cli.core.command_system import CommandContext, CommandRegistry
from aworld_cli.plugin_framework.commands import register_plugin_commands
from aworld_cli.plugin_framework.discovery import discover_plugins


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
