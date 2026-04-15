from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aworld_cli.gateway_cli import (
    build_gateway_parser,
    extract_gateway_argv,
    find_gateway_command_index,
    handle_gateway_status,
)
from aworld_gateway.http.server import create_gateway_app


def test_gateway_parser_accepts_status_and_channels_list() -> None:
    parser = build_gateway_parser()

    status_args = parser.parse_args(["status"])
    list_args = parser.parse_args(["channels", "list"])

    assert status_args.gateway_action == "status"
    assert list_args.gateway_action == "channels"
    assert list_args.channels_action == "list"


def test_gateway_parser_does_not_expose_serve_before_task_6() -> None:
    parser = build_gateway_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["serve"])


def test_extract_gateway_argv_ignores_global_flags_before_gateway() -> None:
    argv = ["aworld-cli", "--zh", "--examples", "gateway", "status"]

    assert extract_gateway_argv(argv) == ["status"]


def test_find_gateway_command_index_ignores_option_values() -> None:
    argv = ["aworld-cli", "--agent", "gateway", "list"]

    assert find_gateway_command_index(argv) is None
    assert extract_gateway_argv(argv) == []


def test_gateway_status_is_read_only(tmp_path: Path) -> None:
    status = handle_gateway_status(base_dir=tmp_path)

    assert status["state"] == "registered"
    assert not (tmp_path / ".aworld" / "gateway" / "config.yaml").exists()


def test_gateway_http_app_exposes_health_and_channel_status() -> None:
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

    client = TestClient(app)
    routes = {route.path for route in app.routes}

    assert "/healthz" in routes
    assert "/channels" in routes
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/channels").json() == {
        "telegram": {
            "enabled": False,
            "implemented": True,
            "state": "registered",
        }
    }
