from pathlib import Path

from aworld_cli.core.command_system import Command, CommandContext, CommandRegistry

from .resources import PluginResourceResolver


class PluginPromptCommand(Command):
    def __init__(self, plugin, entrypoint):
        self._plugin = plugin
        self._entrypoint = entrypoint
        self._resolver = PluginResourceResolver(Path(plugin.manifest.plugin_root), plugin.manifest.plugin_id)

    @property
    def name(self) -> str:
        return self._entrypoint.name or self._entrypoint.entrypoint_id

    @property
    def description(self) -> str:
        return self._entrypoint.description or ""

    @property
    def allowed_tools(self) -> list[str]:
        tools = self._entrypoint.permissions.get("allowed_tools", [])
        return [str(item) for item in tools]

    def resolve_state_path(self, context: CommandContext):
        runtime = getattr(context, "runtime", None)
        if runtime is None or not hasattr(runtime, "_resolve_plugin_state_path"):
            return None

        session_id = getattr(runtime, "session_id", None)
        return runtime._resolve_plugin_state_path(
            plugin_id=self._plugin.manifest.plugin_id,
            scope=self._entrypoint.scope,
            session_id=session_id,
            workspace_path=context.cwd,
        )

    async def get_prompt(self, context: CommandContext) -> str:
        prompt_path = self._resolver.resolve_asset(self._entrypoint.target)
        prompt = prompt_path.read_text(encoding="utf-8")
        return f"{prompt}\n\nUser args: {context.user_args}".strip()


def register_plugin_commands(plugins) -> None:
    for plugin in plugins:
        for entrypoint in plugin.manifest.entrypoints.get("commands", []):
            if entrypoint.visibility == "hidden":
                continue
            command_name = entrypoint.name or entrypoint.entrypoint_id
            if CommandRegistry.get(command_name) is not None:
                continue
            CommandRegistry.register(PluginPromptCommand(plugin, entrypoint))
