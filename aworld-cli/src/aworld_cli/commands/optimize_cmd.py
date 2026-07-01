"""
/optimize command - Run framework self-evolve optimization from chat.
"""
from __future__ import annotations

import argparse
import asyncio
import shlex

from aworld_cli.core.command_system import Command, CommandContext, register_command
from aworld_cli.top_level_commands.optimize_cmd import (
    drain_pending_self_evolve_jobs,
    render_optimize_summary,
    run_optimize_cli,
)


def _usage() -> str:
    return """Usage:
  /optimize --from-trajectory <trajectory.log> --apply proposal [--target <target>]
  /optimize --from-trajectory <trajectory.log> --apply auto_verified --judge-agent <agent.md>
  /optimize --target skill:<name> --dataset <eval.jsonl> --apply proposal
  /optimize --drain-pending

Examples:
  /optimize --from-trajectory ~/Documents/task.log --apply proposal
  /optimize --from-trajectory ~/Documents/task.log --apply auto_verified --judge-agent ~/Documents/agent.md
  /optimize --target skill:media_comprehension --dataset ./eval.jsonl --apply proposal
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="/optimize", add_help=False)
    parser.add_argument("--agent")
    parser.add_argument("--task")
    parser.add_argument("--target")
    parser.add_argument("--dataset")
    parser.add_argument("--from-session", dest="from_session")
    parser.add_argument("--from-trajectory", dest="from_trajectory")
    parser.add_argument("--batch-config", dest="batch_config")
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--apply", default="proposal")
    parser.add_argument("--judge-agent", dest="judge_agent")
    parser.add_argument("--judge-agent-name", dest="judge_agent_name")
    parser.add_argument("--judge-backend-ref", dest="judge_backend_ref")
    parser.add_argument("--replay-timeout", type=int, dest="replay_timeout_seconds")
    parser.add_argument("--replay-max-runs", type=int, dest="replay_max_steps")
    parser.add_argument("--judge-repetitions", type=int, dest="judge_repetitions")
    parser.add_argument("--judge-timeout", type=int, dest="judge_timeout_seconds")
    parser.add_argument("--baseline-replay-repetitions", type=int, dest="baseline_replay_repetitions")
    parser.add_argument("--candidate-replay-repetitions", type=int, dest="candidate_replay_repetitions")
    parser.add_argument("--drain-pending", action="store_true", dest="drain_pending")
    parser.add_argument("--help", action="store_true")
    return parser


@register_command
class OptimizeCommand(Command):
    @property
    def name(self) -> str:
        return "optimize"

    @property
    def description(self) -> str:
        return "Run self-evolve optimization"

    @property
    def command_type(self) -> str:
        return "tool"

    @property
    def completion_items(self) -> dict[str, str]:
        return {
            "/optimize --from-trajectory": "Run self-evolve from an AWorld trajectory log",
            "/optimize --apply auto_verified": "Run verified replay/evaluation before applying",
            "/optimize --drain-pending": "Drain pending post-run self-evolve jobs",
        }

    async def execute(self, context: CommandContext) -> str:
        raw_args = (context.user_args or "").strip()
        if not raw_args:
            return _usage()

        try:
            parts = shlex.split(raw_args)
        except ValueError as exc:
            return f"Optimize error: {exc}\n\n{_usage()}"

        if not parts or parts[0] in {"help", "--help", "-h"}:
            return _usage()

        parser = _build_parser()
        try:
            args = parser.parse_args(parts)
        except SystemExit:
            return _usage()

        if args.help:
            return _usage()

        if args.drain_pending:
            runtime_registry_refresher = _runtime_registry_refresher(context.runtime)
            drain_kwargs = {"workspace_root": context.cwd}
            if runtime_registry_refresher is not None:
                drain_kwargs["runtime_registry_refresher"] = runtime_registry_refresher
            drained = await asyncio.to_thread(
                drain_pending_self_evolve_jobs,
                **drain_kwargs,
            )
            return f"Drained pending self-evolve jobs: {drained}"

        try:
            runtime_registry_refresher = _runtime_registry_refresher(context.runtime)
            report = await asyncio.to_thread(
                run_optimize_cli,
                agent=args.agent,
                task=args.task,
                target=args.target,
                dataset=args.dataset,
                from_session=args.from_session,
                from_trajectory=args.from_trajectory,
                batch_config=args.batch_config,
                iterations=args.iterations,
                apply=args.apply,
                infer_target=args.target is None,
                workspace_root=context.cwd,
                judge_agent=args.judge_agent,
                judge_agent_name=args.judge_agent_name,
                judge_backend_ref=args.judge_backend_ref,
                judge_repetitions=args.judge_repetitions,
                judge_timeout_seconds=args.judge_timeout_seconds,
                replay_timeout_seconds=args.replay_timeout_seconds,
                replay_max_steps=args.replay_max_steps,
                baseline_replay_repetitions=args.baseline_replay_repetitions,
                candidate_replay_repetitions=args.candidate_replay_repetitions,
                runtime_registry_refresher=runtime_registry_refresher,
            )
        except (FileNotFoundError, ValueError, KeyError, NotImplementedError) as exc:
            return f"Optimize error: {exc}"

        return render_optimize_summary(report)


def _runtime_registry_refresher(runtime):
    refresher = getattr(runtime, "refresh_skill_registry", None)
    return refresher if callable(refresher) else None
