from __future__ import annotations

import argparse
from pathlib import Path

from aworld_gateway.config import GatewayConfigLoader
from aworld_gateway.registry import ChannelRegistry
from aworld_gateway.runtime import GatewayRuntime


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

    subparsers.add_parser("serve", help="Start the gateway service")
    subparsers.add_parser("status", help="Show gateway status")

    channels_parser = subparsers.add_parser("channels", help="Channel operations")
    channel_subparsers = channels_parser.add_subparsers(
        dest="channels_action",
        required=True,
    )
    channel_subparsers.add_parser("list", help="List registered channels")
    return parser


def handle_gateway_status(base_dir: Path | str | None = None) -> dict[str, object]:
    resolved_base_dir = Path.cwd() if base_dir is None else Path(base_dir)
    config = GatewayConfigLoader(base_dir=resolved_base_dir).load_or_init()
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )
    return runtime.status()


def handle_gateway_channels_list() -> dict[str, dict[str, object]]:
    return ChannelRegistry().list_channels()
