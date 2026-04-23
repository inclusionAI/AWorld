from __future__ import annotations

import argparse
import asyncio
from typing import Sequence

from aworld.logs.util import logger
from aworld.memory.main import _default_file_memory_store


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


def _find_interactive_command_index(argv: Sequence[str]) -> int | None:
    from aworld_cli.main import _GLOBAL_OPTIONS_WITH_VALUES

    index = 1 if argv else 0
    while index < len(argv):
        token = argv[index]
        if token in _GLOBAL_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        if token == "interactive":
            return index
        return None
    return None


def _parse_interactive_invocation_args(argv: Sequence[str]) -> argparse.Namespace:
    interactive_index = _find_interactive_command_index(argv)
    if interactive_index is None:
        return _build_interactive_parser(add_help=False).parse_args([])

    parse_argv = list(argv[1:interactive_index]) + list(argv[interactive_index + 1 :])
    args, _ = _build_interactive_parser(add_help=False).parse_known_args(parse_argv)
    return args


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
        from aworld_cli._globals import console
        from aworld_cli.core.config import has_model_config, load_config_with_env
        from aworld_cli.core.skill_registry import get_skill_registry
        from aworld_cli.main import (
            _resolve_agent_dirs,
            _run_interactive_mode,
            _show_banner,
            init_middlewares,
        )

        invocation_args = _parse_interactive_invocation_args(context.argv)
        config_dict, _, _ = load_config_with_env(invocation_args.env_file)
        init_middlewares(
            init_memory=True,
            init_retriever=False,
            custom_memory_store=_default_file_memory_store(),
        )

        if "--no-banner" not in context.argv:
            _show_banner()

        if not has_model_config(config_dict):
            console.print(
                "[yellow]No model configuration (API key, etc.) detected. Please configure before starting.[/yellow]"
            )
            console.print("[dim]Run: aworld-cli --config[/dim]")
            console.print(
                "[dim]Or create .env in the current directory. See: [link=https://github.com/inclusionAI/AWorld/blob/main/README.md]README[/link][/dim]"
            )
            return 1

        if invocation_args.skill_path:
            registry = get_skill_registry(skill_paths=invocation_args.skill_path)
        else:
            registry = get_skill_registry()

        all_skills = registry.get_all_skills()
        if all_skills:
            skill_names = list(all_skills.keys())
            logger.info(
                "Loaded %d global skill(s): %s",
                len(skill_names),
                ", ".join(skill_names),
            )

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
