from __future__ import annotations

import argparse
from typing import Sequence


GLOBAL_OPTIONS_WITH_VALUES = {
    "--agent",
    "--task",
    "--max-runs",
    "--max-cost",
    "--max-duration",
    "--completion-signal",
    "--completion-threshold",
    "--session_id",
    "--session-id",
    "--env-file",
    "--remote-backend",
    "--agent-dir",
    "--agent-file",
    "--skill-path",
    "--http-host",
    "--http-port",
    "--mcp-name",
    "--mcp-transport",
    "--mcp-host",
    "--mcp-port",
}


def find_acp_command_index(argv: Sequence[str]) -> int | None:
    index = 1 if argv else 0

    while index < len(argv):
        token = argv[index]
        if token in GLOBAL_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        if token == "acp":
            return index
        return None

    return None


def build_acp_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ACP backend host commands",
        prog="aworld-cli acp",
    )
    subparsers = parser.add_subparsers(dest="acp_action")
    subparsers.required = False
    subparsers.add_parser("serve", help="Run the ACP stdio host")
    subparsers.add_parser("self-test", help="Run ACP self-validation")
    parser.set_defaults(acp_action="serve")
    return parser
