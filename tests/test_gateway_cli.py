from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aworld_cli.main as cli_main
from aworld_cli.gateway_cli import (
    build_gateway_parser,
    extract_gateway_argv,
    find_gateway_command_index,
    handle_gateway_channels_list,
    handle_gateway_status,
)
from aworld_gateway.http.server import create_gateway_app


def test_gateway_parser_accepts_status_and_channels_list() -> None:
    parser = build_gateway_parser()

    status_args = parser.parse_args(["status"])
    list_args = parser.parse_args(["channels", "list"])
    serve_args = parser.parse_args(["serve"])

    assert status_args.gateway_action == "status"
    assert list_args.gateway_action == "channels"
    assert list_args.channels_action == "list"
    assert serve_args.gateway_action == "serve"


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


def test_gateway_channels_list_is_read_only(tmp_path: Path) -> None:
    rows = handle_gateway_channels_list(base_dir=tmp_path)

    assert "telegram" in rows
    assert not (tmp_path / ".aworld" / "gateway" / "config.yaml").exists()


@pytest.mark.parametrize(
    ("argv", "expected_stdout", "expected_calls"),
    [
        (
            ["aworld-cli", "gateway", "status"],
            "{'state': 'registered'}\n",
            {"status": 1, "channels": 0, "serve": []},
        ),
        (
            ["aworld-cli", "gateway", "channels", "list"],
            "{'telegram': {'enabled': False}}\n",
            {"status": 0, "channels": 1, "serve": []},
        ),
        (
            ["aworld-cli", "--zh", "gateway", "serve"],
            "",
            {
                "status": 0,
                "channels": 0,
                "serve": [
                    {
                        "base_dir": Path.cwd(),
                        "remote_backends": None,
                        "local_dirs": None,
                        "agent_files": None,
                    }
                ],
            },
        ),
    ],
)
def test_main_dispatches_gateway_actions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    expected_stdout: str,
    expected_calls: dict[str, object],
) -> None:
    calls = {"status": 0, "channels": 0, "serve": []}

    async def fake_serve_gateway(
        *,
        base_dir,
        remote_backends,
        local_dirs,
        agent_files,
    ) -> None:
        calls["serve"].append(
            {
                "base_dir": base_dir,
                "remote_backends": remote_backends,
                "local_dirs": local_dirs,
                "agent_files": agent_files,
            }
        )

    monkeypatch.setattr(
        "aworld_cli.gateway_cli.handle_gateway_status",
        lambda: calls.__setitem__("status", calls["status"] + 1)
        or {"state": "registered"},
    )
    monkeypatch.setattr(
        "aworld_cli.gateway_cli.handle_gateway_channels_list",
        lambda: calls.__setitem__("channels", calls["channels"] + 1)
        or {"telegram": {"enabled": False}},
    )
    monkeypatch.setattr("aworld_cli.gateway_cli.serve_gateway", fake_serve_gateway)
    monkeypatch.setattr(sys, "argv", argv)

    cli_main.main()

    captured = capsys.readouterr()
    assert captured.out == expected_stdout
    assert calls == expected_calls


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
