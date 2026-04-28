from aworld_cli.plugin_capabilities.commands import PluginBoundCommand


class CancelRalphCommand(PluginBoundCommand):
    @property
    def command_type(self) -> str:
        return "tool"

    async def execute(self, context) -> str:
        handle = self.get_state_handle(context)
        if handle is None:
            return "No Ralph session state is available for the current session."
        current = handle.read()
        if not current.get("active"):
            handle.clear()
            return "No active Ralph loop to cancel."
        handle.clear()
        return "Ralph loop cancelled."


def build_command(plugin, entrypoint):
    return CancelRalphCommand(plugin, entrypoint)
