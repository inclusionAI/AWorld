from __future__ import annotations

import argparse
import asyncio

from aworld_cli.runtime_bootstrap import RuntimeBootstrapError, bootstrap_runtime
from aworld_cli.top_level_commands.invocation import parse_command_invocation_args


def _register_interactive_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent",
        type=str,
        help="Agent name to use in interactive mode.",
    )
    parser.add_argument(
        "--skill",
        dest="skill",
        action="append",
        help="Explicitly request an installed skill by name.",
    )
    parser.add_argument("--env-file", type=str, default=".env")
    parser.add_argument("--remote-backend", type=str, action="append")
    parser.add_argument("--agent-dir", type=str, action="append")
    parser.add_argument("--agent-file", type=str, action="append")
    parser.add_argument("--skill-path", type=str, action="append")


def _build_interactive_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aworld-cli interactive",
        description="Start an interactive agent session.",
        add_help=add_help,
    )
    _register_interactive_options(parser)
    return parser


def _parse_interactive_invocation_args(argv) -> argparse.Namespace:
    from aworld_cli.main import _GLOBAL_OPTIONS_WITH_VALUES

    return parse_command_invocation_args(
        argv,
        command_name="interactive",
        parser=_build_interactive_parser(add_help=False),
        options_with_values=_GLOBAL_OPTIONS_WITH_VALUES,
    )


class InteractiveTopLevelCommand:
    @property
    def name(self) -> str:
        return "interactive"

    @property
    def description(self) -> str:
        return "Start an interactive agent session."

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        command_parser = subparsers.add_parser(
            "interactive",
            help=self.description,
            description=self.description,
            prog="aworld-cli interactive",
        )
        _register_interactive_options(command_parser)

    def run(self, args, context) -> int | None:
        from aworld_cli.main import (
            _resolve_agent_dirs,
            _run_interactive_mode,
            _show_banner,
            init_middlewares,
        )

        invocation_args = _parse_interactive_invocation_args(context.argv)
        try:
            bootstrap_runtime(
                env_file=invocation_args.env_file,
                skill_paths=invocation_args.skill_path,
                show_banner="--no-banner" not in context.argv,
                init_middlewares_fn=init_middlewares,
                show_banner_fn=_show_banner,
            )
        except RuntimeBootstrapError:
            return 1

        asyncio.run(
            _run_interactive_mode(
                agent_name=invocation_args.agent or "Aworld",
                requested_skill_names=invocation_args.skill,
                remote_backends=invocation_args.remote_backend,
                local_dirs=_resolve_agent_dirs(invocation_args.agent_dir),
                agent_files=invocation_args.agent_file,
            )
        )
        return 0
