"""Lightweight entrypoint containing only the HTTP Cloud CLI surface."""

from __future__ import annotations

import argparse
import sys

from aworld_cli.top_level_commands.cloud_cmd import CloudTopLevelCommand


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aworld-cloud-cli")
    commands = parser.add_subparsers(dest="command", required=True)
    command = CloudTopLevelCommand()
    command.register_parser(commands)
    args = parser.parse_args(argv)
    return command.run(args, None)


if __name__ == "__main__":
    sys.exit(main())
