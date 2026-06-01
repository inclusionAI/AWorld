from aworld_cli.core.command_system import CommandContext
from aworld_cli.plugin_capabilities.commands import PluginBoundCommand

from aworld_cli.builtin_plugins.goal_session.common import (
    parse_goal_args,
    resolve_goal_control_action,
)
from aworld_cli.builtin_plugins.goal_session.hooks.task_completed import (
    build_goal_context_prompt,
    goal_status,
    is_goal_active,
    new_goal_contract_state,
)


class GoalCommand(PluginBoundCommand):
    @property
    def command_type(self) -> str:
        return "prompt"

    def resolve_command_type(self, context: CommandContext) -> str:
        if resolve_goal_control_action(context.user_args):
            return "tool"
        return "prompt"

    def should_start_new_session(self, context: CommandContext) -> bool:
        return resolve_goal_control_action(context.user_args) is None

    async def pre_execute(self, context: CommandContext):
        if resolve_goal_control_action(context.user_args):
            return None
        try:
            parse_goal_args(context.user_args)
        except ValueError as exc:
            return f"/goal error: {exc}"
        if self.get_state_handle(context) is None:
            return "/goal requires session-aware plugin state"
        return None

    def _build_start_state(self, context: CommandContext) -> dict:
        parsed = parse_goal_args(context.user_args)
        handle = self.get_state_handle(context)
        if handle is None:
            raise ValueError("/goal requires session-aware plugin state")
        state = new_goal_contract_state(
            objective=parsed["prompt"],
            verification_commands=parsed["verify_commands"],
            completion_promise=parsed["completion_promise"],
            max_turns=parsed["max_turns"],
            source="goal",
        )
        handle.write(state)
        return state

    async def execute(self, context: CommandContext) -> str:
        action = resolve_goal_control_action(context.user_args)
        if action is None:
            try:
                state = self._build_start_state(context)
            except ValueError as exc:
                return f"/goal error: {exc}"
            return build_goal_context_prompt(state)

        handle = self.get_state_handle(context)
        if handle is None:
            return "Goal session state is unavailable."

        current = handle.read()

        if action == "status":
            if goal_status(current) == "none":
                return "No active goal."
            return build_goal_context_prompt(current)

        if action == "pause":
            if not is_goal_active(current):
                return "No active goal to pause."
            updated = handle.update(
                {
                    "active": False,
                    "status": "paused",
                    "last_task_status": "paused",
                }
            )
            return build_goal_context_prompt(updated)

        if action == "clear":
            if not current:
                return "No goal state to clear."
            handle.clear()
            return "Goal cleared."

        return "Unknown /goal action."

    async def get_prompt(self, context: CommandContext) -> str:
        state = self._build_start_state(context)
        return build_goal_context_prompt(state)


def handle_event(event, state):
    if not is_goal_active(state):
        return {"action": "allow"}

    return {
        "action": "deny",
        "reason": "An active goal is still in progress. Use /goal pause to keep it for later or /goal clear to discard it before exiting.",
    }


def build_command(plugin, entrypoint):
    return GoalCommand(plugin, entrypoint)
