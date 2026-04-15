from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from aworld_gateway.config import GatewayConfigLoader
from aworld_gateway.config import GatewayConfig
from aworld_gateway.registry import ChannelRegistry
from aworld_gateway.runtime import GatewayRuntime

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


def build_gateway_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gateway management commands",
        prog="aworld-cli gateway",
    )
    subparsers = parser.add_subparsers(
        dest="gateway_action",
        help="Gateway action to perform",
        required=True,
    )

    subparsers.add_parser("status", help="Show gateway status")

    channels_parser = subparsers.add_parser("channels", help="Channel operations")
    channel_subparsers = channels_parser.add_subparsers(
        dest="channels_action",
        required=True,
    )
    channel_subparsers.add_parser("list", help="List registered channels")
    return parser


def find_gateway_command_index(argv: Sequence[str]) -> int | None:
    index = 1 if argv else 0

    while index < len(argv):
        token = argv[index]
        if token in GLOBAL_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        if token == "gateway":
            return index
        return None

    return None


def extract_gateway_argv(argv: Sequence[str]) -> list[str]:
    gateway_index = find_gateway_command_index(argv)
    if gateway_index is None:
        return []
    return list(argv[gateway_index + 1 :])


def handle_gateway_status(base_dir: Path | str | None = None) -> dict[str, object]:
    resolved_base_dir = Path.cwd() if base_dir is None else Path(base_dir)
    loader = GatewayConfigLoader(base_dir=resolved_base_dir)
    if loader.config_path.exists():
        config = loader.load_or_init()
    else:
        config = GatewayConfig()
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )
    return runtime.status()


def handle_gateway_channels_list() -> dict[str, dict[str, object]]:
    return ChannelRegistry().list_channels()
