from aworld_cli.core.command_system import CommandContext
from aworld_cli.plugin_capabilities.commands import PluginBoundCommand

from aworld_cli.builtin_plugins.ralph_session_loop.common import (
    build_loop_prompt,
    parse_loop_args,
    started_at_iso,
)


class RalphLoopCommand(PluginBoundCommand):
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
        prompt = build_loop_prompt(
            parsed["prompt"],
            parsed["verify_commands"],
            parsed["completion_promise"],
        )
        handle = self.get_state_handle(context)
        if handle is None:
            raise ValueError("/ralph-loop requires session-aware plugin state")
        handle.write(
            {
                "active": True,
                "prompt": parsed["prompt"],
                "iteration": 1,
                "max_iterations": parsed["max_iterations"],
                "completion_promise": parsed["completion_promise"],
                "verify_commands": parsed["verify_commands"],
                "started_at": started_at_iso(),
                "last_stop_reason": None,
                "last_final_answer": "",
                "last_final_answer_excerpt": None,
                "last_task_status": "initialized",
            }
        )
        return prompt


def build_command(plugin, entrypoint):
    return RalphLoopCommand(plugin, entrypoint)
