import shlex

from aworld_cli.core.command_system import CommandContext
from aworld_cli.plugin_capabilities.commands import PluginBoundCommand

from aworld_cli.builtin_plugins.goal_session.hooks.task_completed import (
    build_goal_context_prompt,
    goal_status,
    is_goal_active,
)


class GoalCommand(PluginBoundCommand):
    @property
    def command_type(self) -> str:
        return "tool"

    async def execute(self, context: CommandContext) -> str:
        handle = self.get_state_handle(context)
        if handle is None:
            return "Goal session state is unavailable."

        tokens = shlex.split(context.user_args or "")
        action = (tokens[0] if tokens else "status").strip().lower()
        current = handle.read()

        if action == "status":
            if goal_status(current) == "none":
                return "No active goal."
            return build_goal_context_prompt(current)

        if action == "pause":
            if not is_goal_active(current):
                return "No active goal to pause."
            updated = handle.update({"active": False, "status": "paused"})
            return build_goal_context_prompt(updated)

        if action == "clear":
            if not current:
                return "No goal state to clear."
            handle.clear()
            return "Goal cleared."

        return "Usage: /goal [status|pause|clear]"


def handle_event(event, state):
    if not is_goal_active(state):
        return {"action": "allow"}

    return {
        "action": "deny",
        "reason": "An active goal is still in progress. Use /goal pause to keep it for later or /goal clear to discard it before exiting.",
    }


def build_command(plugin, entrypoint):
    return GoalCommand(plugin, entrypoint)
