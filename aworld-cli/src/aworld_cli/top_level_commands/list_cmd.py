from __future__ import annotations

import argparse
import asyncio
from typing import Sequence


def _build_list_global_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--remote-backend", action="append")
    parser.add_argument("--agent-dir", action="append")
    parser.add_argument("--agent-file", action="append")
    return parser


def _find_list_command_index(argv: Sequence[str]) -> int | None:
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
        if token == "list":
            return index
        return None
    return None


def _parse_list_global_args(argv: Sequence[str]) -> argparse.Namespace:
    list_index = _find_list_command_index(argv)
    if list_index is None:
        return _build_list_global_parser().parse_args([])

    args, _ = _build_list_global_parser().parse_known_args(argv[1:list_index])
    return args


class ListTopLevelCommand:
    @property
    def name(self) -> str:
        return "list"

    @property
    def description(self) -> str:
        return "List available agents"

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        subparsers.add_parser(
            "list",
            help=self.description,
            description=self.description,
            prog="aworld-cli list",
        )

    def run(self, args, context) -> int | None:
        from aworld_cli.main import AWorldCLI, _resolve_agent_dirs, load_all_agents

        global_args = _parse_list_global_args(context.argv)
        cli = AWorldCLI()
        all_agents = asyncio.run(
            load_all_agents(
                remote_backends=global_args.remote_backend,
                local_dirs=_resolve_agent_dirs(global_args.agent_dir),
                agent_files=global_args.agent_file,
            )
        )

        if all_agents:
            cli.display_agents(all_agents)
        else:
            print("❌ No agents found from any source.")
        return 0
