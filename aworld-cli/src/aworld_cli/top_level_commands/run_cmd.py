from __future__ import annotations

import argparse
import asyncio

from aworld_cli.runtime_bootstrap import RuntimeBootstrapError, bootstrap_runtime


def _register_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=str, required=True)
    parser.add_argument("--agent", type=str)
    parser.add_argument("--skill", dest="skill", action="append")
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--max-cost", type=float)
    parser.add_argument("--max-duration", type=str)
    parser.add_argument("--completion-signal", type=str)
    parser.add_argument("--completion-threshold", type=int, default=3)
    parser.add_argument("--session_id", "--session-id", type=str, dest="session_id")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--env-file", type=str, default=".env")
    parser.add_argument("--remote-backend", type=str, action="append")
    parser.add_argument("--agent-dir", type=str, action="append")
    parser.add_argument("--agent-file", type=str, action="append")
    parser.add_argument("--skill-path", type=str, action="append")


class RunTopLevelCommand:
    @property
    def name(self) -> str:
        return "run"

    @property
    def description(self) -> str:
        return "Run a task in direct mode."

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser(
            "run",
            help=self.description,
            description=self.description,
            prog="aworld-cli run",
        )
        _register_run_options(parser)

    def run(self, args, context) -> int | None:
        from aworld_cli.main import (
            _resolve_agent_dirs,
            _run_direct_mode,
            _show_banner,
            init_middlewares,
        )

        try:
            bootstrap_runtime(
                env_file=args.env_file,
                skill_paths=args.skill_path,
                show_banner="--no-banner" not in context.argv,
                init_middlewares_fn=init_middlewares,
                show_banner_fn=_show_banner,
            )
        except RuntimeBootstrapError:
            return 1

        local_dirs = _resolve_agent_dirs(args.agent_dir)
        agent_name = self._resolve_agent_name(args)
        if agent_name is None:
            return 0

        asyncio.run(
            _run_direct_mode(
                prompt=args.task,
                agent_name=agent_name,
                requested_skill_names=args.skill,
                max_runs=args.max_runs,
                max_cost=args.max_cost,
                max_duration=args.max_duration,
                completion_signal=args.completion_signal,
                completion_threshold=args.completion_threshold,
                non_interactive=args.non_interactive,
                session_id=args.session_id,
                remote_backends=args.remote_backend,
                local_dirs=local_dirs,
                agent_files=args.agent_file,
            )
        )
        return 0

    def _resolve_agent_name(self, args) -> str | None:
        agent_name = args.agent
        if not agent_name and args.agent_file:
            if len(args.agent_file) == 1:
                from aworld_cli.core.loader import init_agent_file

                try:
                    agent_name = init_agent_file(args.agent_file[0])
                    if not agent_name:
                        print(
                            f"❌ Error: Could not extract agent name from {args.agent_file[0]}"
                        )
                        return None
                    print(f"ℹ️  Auto-detected agent name: {agent_name}")
                except Exception as exc:
                    print(
                        f"❌ Error: Failed to load agent file {args.agent_file[0]}: {exc}"
                    )
                    return None
            else:
                print("❌ Error: --agent is required when using multiple --agent-file")
                return None
        elif not agent_name:
            agent_name = "Aworld"
            print(f"ℹ️  Using default agent: {agent_name}")

        return agent_name
