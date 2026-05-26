from aworld_cli.core.command_system import CommandContext
from aworld_cli.plugin_capabilities.commands import PluginBoundCommand

from aworld_cli.builtin_plugins.goal_session.hooks.task_completed import (
    build_goal_context_prompt,
    new_goal_contract_state,
)
from aworld_cli.builtin_plugins.ralph_session_loop.common import parse_loop_args


GOAL_SESSION_PLUGIN_ID = "goal-session"


class RalphLoopCommand(PluginBoundCommand):
    def resolve_state_path(self, context: CommandContext):
        runtime = getattr(context, "runtime", None)
        if runtime is None or not hasattr(runtime, "_resolve_plugin_state_path"):
            return None
        return runtime._resolve_plugin_state_path(
            plugin_id=GOAL_SESSION_PLUGIN_ID,
            scope="session",
            session_id=self._resolve_session_id(context),
            workspace_path=context.cwd,
        )

    async def pre_execute(self, context: CommandContext):
        try:
            parse_loop_args(context.user_args)
        except ValueError as exc:
            return f"/ralph-loop error: {exc}"
        if self.get_state_handle(context) is None:
            return "/ralph-loop requires session-aware plugin state"
        return None

    async def get_prompt(self, context: CommandContext) -> str:
        parsed = parse_loop_args(context.user_args)
        handle = self.get_state_handle(context)
        if handle is None:
            raise ValueError("/ralph-loop requires session-aware plugin state")
        state = new_goal_contract_state(
            objective=parsed["prompt"],
            verification_commands=parsed["verify_commands"],
            completion_promise=parsed["completion_promise"],
            max_turns=parsed["max_iterations"],
            source="ralph_compat",
        )
        handle.write(state)
        return build_goal_context_prompt(state)


def build_command(plugin, entrypoint):
    return RalphLoopCommand(plugin, entrypoint)
