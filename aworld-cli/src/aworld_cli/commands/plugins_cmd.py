"""
/plugins command - List available CLI plugins.
"""
from aworld_cli.core.command_system import Command, CommandContext, register_command
from aworld_cli.core.plugin_manager import (
    PluginManager,
    list_available_plugins,
    render_plugins_table,
)


@register_command
class PluginsCommand(Command):
    def _refresh_runtime(self, context: CommandContext) -> str:
        runtime = getattr(context, "runtime", None)
        if runtime is None or not hasattr(runtime, "refresh_plugin_framework"):
            return ""

        try:
            runtime.refresh_plugin_framework()
        except Exception as exc:
            return f"\nWarning: current session plugin state refresh failed: {exc}"

        return "\nCurrent session plugins refreshed."

    @property
    def name(self) -> str:
        return "plugins"

    @property
    def description(self) -> str:
        return "Manage CLI plugins"

    @property
    def command_type(self) -> str:
        return "tool"

    @property
    def completion_items(self) -> dict[str, str]:
        return {
            "/plugins list": "List available plugins",
            "/plugins enable": "Enable an installed framework plugin",
            "/plugins disable": "Disable an installed framework plugin",
            "/plugins reload": "Reload plugin metadata from disk",
        }

    async def execute(self, context: CommandContext) -> str:
        user_args = (context.user_args or "").strip()
        if not user_args or user_args == "list":
            manager = PluginManager()
            return render_plugins_table(list_available_plugins(manager), manager.plugin_dir)

        parts = user_args.split(maxsplit=1)
        action = parts[0].lower()
        plugin_name = parts[1].strip() if len(parts) > 1 else ""

        if action in {"enable", "disable", "reload"}:
            if not plugin_name:
                return f"Usage: /plugins {action} <plugin_name>"

            manager = PluginManager()
            try:
                if action == "enable":
                    plugin_state = manager.enable(plugin_name)
                    return (
                        f"Plugin '{plugin_name}' enabled\n"
                        f"Location: {plugin_state['path']}"
                        f"{self._refresh_runtime(context)}"
                    )
                if action == "disable":
                    plugin_state = manager.disable(plugin_name)
                    return (
                        f"Plugin '{plugin_name}' disabled\n"
                        f"Location: {plugin_state['path']}"
                        f"{self._refresh_runtime(context)}"
                    )

                plugin_state = manager.reload(plugin_name)
                return (
                    f"Plugin '{plugin_name}' reloaded\n"
                    f"Location: {plugin_state['path']}"
                    f"{self._refresh_runtime(context)}"
                )
            except KeyError:
                return f"Plugin '{plugin_name}' is not installed"

        return (
            f"Unknown plugins subcommand: {user_args}\n\n"
            "Supported commands:\n"
            "- /plugins\n"
            "- /plugins list"
            "- /plugins enable <plugin_name>\n"
            "- /plugins disable <plugin_name>\n"
            "- /plugins reload <plugin_name>"
        )
