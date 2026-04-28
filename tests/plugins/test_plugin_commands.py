from types import SimpleNamespace

from pathlib import Path

import pytest

from aworld_cli.core.command_system import CommandContext, CommandRegistry
from aworld_cli.builtin_plugins.ralph_session_loop.common import (
    extract_completion_promise,
    parse_loop_args,
    summarize_text,
)
from aworld.plugins.discovery import discover_plugins
from aworld_cli.plugin_capabilities.commands import PluginPromptCommand, register_plugin_commands, sync_plugin_commands
from aworld_cli.plugin_capabilities.state import PluginStateStore
from aworld_cli.runtime.base import BaseCliRuntime


def _build_dummy_runtime(tmp_path):
    class DummyRuntime(BaseCliRuntime):
        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://commands"

    runtime = DummyRuntime(agent_name="Aworld")
    runtime._plugin_state_store = PluginStateStore(tmp_path / "state")
    return runtime


def _get_builtin_ralph_plugin_root() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "aworld-cli"
        / "src"
        / "aworld_cli"
        / "builtin_plugins"
        / "ralph_session_loop"
    )


@pytest.mark.parametrize("user_args", ['"Build API" --max-iterations 0', '"Build API" --max-iterations -5'])
def test_parse_loop_args_rejects_non_positive_max_iterations(user_args):
    with pytest.raises(ValueError, match="--max-iterations must be >= 1"):
        parse_loop_args(user_args)


def test_parse_loop_args_rejects_malformed_quotes():
    with pytest.raises(ValueError, match="quotation"):
        parse_loop_args('"Build API --verify unclosed')


def test_extract_completion_promise_strips_multiline_content():
    answer = "Done\n<promise>\nCOMPLETE\n</promise>"

    assert extract_completion_promise(answer) == "COMPLETE"


def test_extract_completion_promise_strips_surrounding_whitespace():
    assert extract_completion_promise("<promise>  COMPLETE  </promise>") == "COMPLETE"


def test_summarize_text_handles_edge_cases():
    assert summarize_text("") == ""
    assert summarize_text("a" * 160) == "a" * 160
    assert summarize_text("a" * 161) == "a" * 157 + "..."
    assert summarize_text("Hello 🎉 World", limit=10) == "Hello 🎉..."
    assert summarize_text(None) is None


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


def test_register_python_backed_plugin_command_from_manifest(tmp_path):
    plugin_root = tmp_path / "python_plugin"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    (plugin_root / "commands").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        (
            "{"
            "\"id\": \"python-plugin\", "
            "\"name\": \"python-plugin\", "
            "\"version\": \"1.0.0\", "
            "\"entrypoints\": {"
            "\"commands\": ["
            "{"
            "\"id\": \"python-backed\", "
            "\"name\": \"python-backed\", "
            "\"target\": \"commands/python_backed.py\""
            "}"
            "]"
            "}"
            "}"
        ),
        encoding="utf-8",
    )
    (plugin_root / "commands" / "python_backed.py").write_text(
        "from aworld_cli.core.command_system import Command\n"
        "class PythonBackedCommand(Command):\n"
        "    @property\n"
        "    def name(self):\n"
        "        return 'python-backed'\n"
        "    @property\n"
        "    def description(self):\n"
        "        return 'Python backed command'\n"
        "    async def get_prompt(self, context):\n"
        "        return f'hello {context.user_args}'\n"
        "def build_command(plugin, entrypoint):\n"
        "    return PythonBackedCommand()\n",
        encoding="utf-8",
    )

    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("python-backed")
        assert command is not None
        assert command.command_type == "prompt"
        prompt = __import__("asyncio").run(
            command.get_prompt(CommandContext(cwd=str(plugin_root), user_args="world"))
        )
        assert prompt == "hello world"
    finally:
        CommandRegistry.restore(snapshot)


def test_command_context_carries_executor_session_id():
    context = CommandContext(
        cwd="/tmp",
        user_args="--flag",
        runtime=SimpleNamespace(),
        session_id="session-123",
    )

    assert context.session_id == "session-123"


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


async def test_ralph_loop_command_initializes_session_state(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("ralph-loop")
        runtime = _build_dummy_runtime(tmp_path)
        workspace_path = str(tmp_path / "workspace")

        prompt = await command.get_prompt(
            CommandContext(
                cwd=workspace_path,
                user_args='"Build a REST API" --verify "pytest tests/api -q" --completion-promise "COMPLETE" --max-iterations 5',
                runtime=runtime,
                session_id="session-1",
            )
        )

        state_path = runtime._resolve_plugin_state_path(
            plugin_id=plugin.manifest.plugin_id,
            scope="session",
            session_id="session-1",
            workspace_path=workspace_path,
        )
        payload = runtime._plugin_state_store.handle(state_path).read()

        assert payload["active"] is True
        assert payload["prompt"] == "Build a REST API"
        assert payload["iteration"] == 1
        assert payload["max_iterations"] == 5
        assert payload["completion_promise"] == "COMPLETE"
        assert payload["verify_commands"] == ["pytest tests/api -q"]
        assert "Task:" in prompt
        assert "Build a REST API" in prompt
        assert "Verification requirements:" in prompt
        assert "1. Run: pytest tests/api -q" in prompt
        assert "Completion rule:" in prompt
        assert "Only output <promise>COMPLETE</promise>" in prompt
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_ralph_loop_command_rejects_missing_state_handle_at_prompt_time(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("ralph-loop")

        with pytest.raises(ValueError, match="session-aware plugin state"):
            await command.get_prompt(
                CommandContext(
                    cwd=str(tmp_path / "workspace"),
                    user_args='"Build API"',
                    runtime=None,
                    session_id=None,
                )
            )
    finally:
        CommandRegistry.restore(snapshot)


async def test_cancel_ralph_clears_session_state(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        runtime = _build_dummy_runtime(tmp_path)
        workspace_path = str(tmp_path / "workspace")
        state_path = runtime._resolve_plugin_state_path(
            plugin_id=plugin.manifest.plugin_id,
            scope="session",
            session_id="session-1",
            workspace_path=workspace_path,
        )
        runtime._plugin_state_store.handle(state_path).write(
            {
                "active": True,
                "prompt": "Build a REST API",
                "iteration": 2,
            }
        )

        command = CommandRegistry.get("cancel-ralph")
        result = await command.execute(
            CommandContext(
                cwd=workspace_path,
                user_args="",
                runtime=runtime,
                session_id="session-1",
            )
        )

        assert "cancel" in result.lower()
        assert runtime._plugin_state_store.handle(state_path).read() == {}
    finally:
        CommandRegistry.restore(snapshot)
