from aworld_cli.plugin_capabilities.commands import PluginBoundCommand


GOAL_SESSION_PLUGIN_ID = "goal-session"


class CancelRalphCommand(PluginBoundCommand):
    def resolve_state_path(self, context):
        runtime = getattr(context, "runtime", None)
        if runtime is None or not hasattr(runtime, "_resolve_plugin_state_path"):
            return None
        return runtime._resolve_plugin_state_path(
            plugin_id=GOAL_SESSION_PLUGIN_ID,
            scope="session",
            session_id=self._resolve_session_id(context),
            workspace_path=context.cwd,
        )

    @property
    def command_type(self) -> str:
        return "tool"

    async def execute(self, context) -> str:
        handle = self.get_state_handle(context)
        if handle is None:
            return "No active Ralph loop to cancel."
        current = handle.read()
        if not current.get("active") or current.get("status") != "active" or current.get("source") != "ralph_compat":
            return "No active Ralph loop to cancel."
        handle.clear()
        return "Ralph loop cancelled."


def build_command(plugin, entrypoint):
    return CancelRalphCommand(plugin, entrypoint)
