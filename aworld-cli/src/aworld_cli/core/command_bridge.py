from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from aworld.plugins.discovery import discover_plugins
from aworld.plugins.resolution import resolve_plugin_activation

from aworld_cli.core.command_system import CommandContext, CommandRegistry
from aworld_cli.core.plugin_manager import PluginManager
from aworld_cli.plugin_capabilities.commands import sync_plugin_commands
from aworld_cli.plugin_capabilities.state import PluginStateStore

_BUILTIN_COMMAND_MODULES: tuple[tuple[str, str], ...] = (
    ("aworld_cli.commands.help_cmd", "help"),
    ("aworld_cli.commands.commit", "commit"),
    ("aworld_cli.commands.review", "review"),
    ("aworld_cli.commands.diff", "diff"),
    ("aworld_cli.commands.history", "history"),
    ("aworld_cli.commands.cron_cmd", "cron"),
    ("aworld_cli.commands.dispatch", "dispatch"),
    ("aworld_cli.commands.tasks", "tasks"),
    ("aworld_cli.commands.plugins_cmd", "plugins"),
)


@dataclass(slots=True)
class CommandBridgeResult:
    handled: bool
    command_name: str | None
    status: str
    text: str


PromptExecutor = Callable[..., Awaitable[str | None]]


class _CommandRuntimeAdapter:
    def __init__(
        self,
        *,
        workspace_path: str,
        session_id: str,
        base_runtime: Any | None = None,
    ) -> None:
        self.workspace_path = str(Path(workspace_path).expanduser().resolve())
        self.session_id = session_id
        self._base_runtime = base_runtime
        self._plugin_state_store = PluginStateStore(
            Path(self.workspace_path) / ".aworld" / "plugin_state"
        )

    def _resolve_plugin_state_path(
        self,
        plugin_id: str,
        scope: str,
        session_id: str | None,
        workspace_path: str | None,
    ) -> Path | None:
        if scope == "global":
            return self._plugin_state_store.global_state(plugin_id)
        if scope == "session" and session_id:
            return self._plugin_state_store.session_state(plugin_id, session_id)
        if workspace_path:
            return self._plugin_state_store.workspace_state(plugin_id, workspace_path)
        return None

    def __getattr__(self, name: str) -> Any:
        if self._base_runtime is None:
            raise AttributeError(name)
        return getattr(self._base_runtime, name)


class CommandBridge:
    def __init__(self, *, plugin_roots: Iterable[str | Path] | None = None) -> None:
        self._plugin_roots = None if plugin_roots is None else [Path(path) for path in plugin_roots]
        self._bootstrapped = False

    @staticmethod
    def is_slash_command(text: str | None) -> bool:
        if not isinstance(text, str):
            return False
        normalized = text.strip()
        return normalized.startswith("/") and len(normalized) > 1

    async def execute(
        self,
        *,
        text: str,
        cwd: str,
        session_id: str,
        runtime: Any | None = None,
        prompt_executor: PromptExecutor | None = None,
        on_output: Callable[[Any], Any] | None = None,
    ) -> CommandBridgeResult:
        normalized = (text or "").strip()
        if not self.is_slash_command(normalized):
            return CommandBridgeResult(
                handled=False,
                command_name=None,
                status="ignored",
                text="",
            )

        self._bootstrap_registry()

        body = normalized[1:].strip()
        if not body:
            return CommandBridgeResult(
                handled=True,
                command_name=None,
                status="unknown",
                text="Unknown command: /\nType /help to see available commands",
            )

        command_name, _, user_args = body.partition(" ")
        command = CommandRegistry.get(command_name)
        if command is None:
            return CommandBridgeResult(
                handled=True,
                command_name=command_name,
                status="unknown",
                text=f"Unknown command: /{command_name}\nType /help to see available commands",
            )

        if command.command_type == "prompt" and prompt_executor is None:
            return CommandBridgeResult(
                handled=True,
                command_name=command_name,
                status="unsupported",
                text=(
                    f"Command '/{command_name}' is not yet supported in this bridge context. "
                    "Prompt commands require a prompt executor."
                ),
            )

        resolved_cwd = str(Path(cwd).expanduser().resolve())
        effective_runtime = _CommandRuntimeAdapter(
            workspace_path=resolved_cwd,
            session_id=session_id,
            base_runtime=runtime,
        )
        context = CommandContext(
            cwd=resolved_cwd,
            user_args=user_args,
            runtime=effective_runtime,
            session_id=session_id,
        )
        self._attach_runtime_capabilities(context, runtime)

        try:
            error = await command.pre_execute(context)
            if error:
                return CommandBridgeResult(
                    handled=True,
                    command_name=command_name,
                    status="error",
                    text=f"Error: {error}",
                )

            if command.command_type == "tool":
                result = await command.execute(context)
            elif command.command_type == "prompt":
                prompt = await command.get_prompt(context)
                result = await prompt_executor(
                    prompt=prompt,
                    allowed_tools=command.allowed_tools or None,
                    on_output=on_output,
                )
            else:
                return CommandBridgeResult(
                    handled=True,
                    command_name=command_name,
                    status="unsupported",
                    text=f"Command '/{command_name}' uses unsupported type '{command.command_type}'.",
                )

            await command.post_execute(context, result)
            if result is None:
                text_result = ""
            else:
                text_result = result if isinstance(result, str) else str(result)
            return CommandBridgeResult(
                handled=True,
                command_name=command_name,
                status="completed",
                text=text_result,
            )
        except Exception as exc:
            return CommandBridgeResult(
                handled=True,
                command_name=command_name,
                status="error",
                text=f"Error executing /{command_name}: {exc}",
            )

    def _bootstrap_registry(self) -> None:
        if self._bootstrapped:
            return

        self._ensure_builtin_commands_loaded()
        sync_plugin_commands(self._resolve_active_plugins())
        self._bootstrapped = True

    def _ensure_builtin_commands_loaded(self) -> None:
        importlib.import_module("aworld_cli.commands")
        for module_name, command_name in _BUILTIN_COMMAND_MODULES:
            if CommandRegistry.get(command_name) is not None:
                continue
            module = importlib.import_module(module_name)
            if CommandRegistry.get(command_name) is None:
                importlib.reload(module)

    def _resolve_active_plugins(self) -> list[Any]:
        plugin_roots = self._plugin_roots
        if plugin_roots is None:
            try:
                plugin_roots = PluginManager().get_runtime_plugin_roots()
            except Exception:
                plugin_roots = []

        discovered_plugins = []
        for plugin_root in plugin_roots:
            try:
                discovered_plugins.extend(discover_plugins([plugin_root]))
            except Exception:
                continue

        active_plugins, _ = resolve_plugin_activation(discovered_plugins)
        return list(active_plugins)

    @staticmethod
    def _attach_runtime_capabilities(context: CommandContext, runtime: Any | None) -> None:
        if runtime is None:
            return

        for attr_name in dir(runtime):
            if not attr_name or attr_name.startswith("_") or hasattr(context, attr_name):
                continue
            try:
                value = getattr(runtime, attr_name)
            except Exception:
                continue
            if callable(value):
                continue
            setattr(context, attr_name, value)
