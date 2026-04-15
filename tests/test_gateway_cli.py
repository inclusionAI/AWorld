from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aworld_cli.gateway_cli import build_gateway_parser
from aworld_gateway.http.server import create_gateway_app


def test_gateway_parser_accepts_status_and_channels_list() -> None:
    parser = build_gateway_parser()

    status_args = parser.parse_args(["status"])
    list_args = parser.parse_args(["channels", "list"])

    assert status_args.gateway_action == "status"
    assert list_args.gateway_action == "channels"
    assert list_args.channels_action == "list"


@pytest.mark.asyncio
async def test_gateway_http_app_exposes_health_and_channel_status() -> None:
    app = create_gateway_app(
        runtime_status={
            "channels": {
                "telegram": {
                    "enabled": False,
                    "implemented": True,
                    "state": "registered",
                }
            }
        }
    )

    routes = {route.path for route in app.routes}

    assert "/healthz" in routes
    assert "/channels" in routes
