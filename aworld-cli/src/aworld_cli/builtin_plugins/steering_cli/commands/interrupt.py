from aworld_cli.core.command_system import CommandContext
from aworld_cli.plugin_capabilities.commands import PluginBoundCommand


class InterruptCommand(PluginBoundCommand):
    @property
    def command_type(self) -> str:
        return "tool"

    async def execute(self, context: CommandContext) -> str:
        runtime = getattr(context, "runtime", None)
        session_id = self._resolve_session_id(context)
        if runtime is None or not hasattr(runtime, "request_session_interrupt"):
            return "Interrupt control is unavailable."

        requested = runtime.request_session_interrupt(session_id)
        return "Interrupt requested." if requested else "No active steerable task."


def build_command(plugin, entrypoint):
    return InterruptCommand(plugin, entrypoint)
