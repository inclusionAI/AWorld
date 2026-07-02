from __future__ import annotations

import argparse
import asyncio

from aworld_cli.top_level_commands.invocation import parse_command_invocation_args


def _build_list_global_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--remote-backend", action="append")
    parser.add_argument("--agent-dir", action="append")
    parser.add_argument("--agent-file", action="append")
    return parser


def _parse_list_global_args(argv) -> argparse.Namespace:
    from aworld_cli.main import _GLOBAL_OPTIONS_WITH_VALUES

    return parse_command_invocation_args(
        argv,
        command_name="list",
        parser=_build_list_global_parser(),
        options_with_values=_GLOBAL_OPTIONS_WITH_VALUES,
    )


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
