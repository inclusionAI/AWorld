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
from aworld_gateway import GATEWAY_DISPLAY_NAME, GATEWAY_IMPORT_NAME


def test_gateway_parser_accepts_status_and_channels_list() -> None:
    parser = build_gateway_parser()

    status_args = parser.parse_args(["status"])
    list_args = parser.parse_args(["channels", "list"])
    server_args = parser.parse_args(["server"])

    assert status_args.gateway_action == "status"
    assert list_args.gateway_action == "channels"
    assert list_args.channels_action == "list"
    assert server_args.gateway_action == "server"


def test_gateway_parser_uses_hyphenated_display_name() -> None:
    parser = build_gateway_parser()

    assert parser.description == "aworld-gateway management commands"


def test_gateway_package_exports_import_and_display_names() -> None:
    assert GATEWAY_DISPLAY_NAME == "aworld-gateway"
    assert GATEWAY_IMPORT_NAME == "aworld_gateway"


def test_gateway_parser_rejects_legacy_serve_action() -> None:
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
            ["aworld-cli", "--zh", "gateway", "server"],
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

    class FakeRegistry:
        def get_all_skills(self):
            return {}

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
    monkeypatch.setattr("aworld_cli.main._show_banner", lambda: None)
    monkeypatch.setattr("aworld_cli.main.init_middlewares", lambda **kwargs: None)
    monkeypatch.setattr("aworld_cli.main._resolve_agent_dirs", lambda agent_dirs: agent_dirs)
    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: ({}, "env", env_file),
    )
    monkeypatch.setattr("aworld_cli.core.config.has_model_config", lambda config: True)
    monkeypatch.setattr(
        "aworld_cli.core.skill_registry.get_skill_registry",
        lambda skill_paths=None: FakeRegistry(),
    )
    monkeypatch.setattr(sys, "argv", argv)

    cli_main.main()

    captured = capsys.readouterr()
    assert captured.out == expected_stdout
    assert calls == expected_calls


def test_main_bootstraps_gateway_server_like_normal_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {
        "load_config_with_env": None,
        "init_middlewares": [],
        "show_banner": 0,
        "get_skill_registry": [],
        "resolve_agent_dirs": [],
        "serve_gateway": [],
    }

    class FakeRegistry:
        def get_all_skills(self):
            return {"demo": object()}

    async def fake_serve_gateway(
        *,
        base_dir,
        remote_backends,
        local_dirs,
        agent_files,
    ) -> None:
        calls["serve_gateway"].append(
            {
                "base_dir": base_dir,
                "remote_backends": remote_backends,
                "local_dirs": local_dirs,
                "agent_files": agent_files,
            }
        )

    monkeypatch.setattr(
        "aworld_cli.gateway_cli.serve_gateway",
        fake_serve_gateway,
    )
    monkeypatch.setattr(
        "aworld_cli.main._show_banner",
        lambda: calls.__setitem__("show_banner", calls["show_banner"] + 1),
    )
    monkeypatch.setattr(
        "aworld_cli.main.init_middlewares",
        lambda **kwargs: calls["init_middlewares"].append(kwargs),
    )
    monkeypatch.setattr(
        "aworld_cli.main._resolve_agent_dirs",
        lambda agent_dirs: calls["resolve_agent_dirs"].append(agent_dirs) or ["./resolved-agents"],
    )
    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: calls.__setitem__("load_config_with_env", env_file)
        or ({"provider": "demo"}, "env", env_file),
    )
    monkeypatch.setattr(
        "aworld_cli.core.config.has_model_config",
        lambda config: True,
    )
    monkeypatch.setattr(
        "aworld_cli.core.skill_registry.get_skill_registry",
        lambda skill_paths=None: calls["get_skill_registry"].append(skill_paths)
        or FakeRegistry(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aworld-cli",
            "--env-file",
            "custom.env",
            "--skill-path",
            "./skills",
            "--agent-dir",
            "./agents",
            "--remote-backend",
            "http://backend",
            "gateway",
            "server",
        ],
    )

    cli_main.main()

    assert calls["load_config_with_env"] == "custom.env"
    assert calls["show_banner"] == 1
    assert len(calls["init_middlewares"]) == 1
    assert calls["get_skill_registry"] == [["./skills"]]
    assert calls["resolve_agent_dirs"] == [["./agents"]]
    assert calls["serve_gateway"] == [
        {
            "base_dir": Path.cwd(),
            "remote_backends": ["http://backend"],
            "local_dirs": ["./resolved-agents"],
            "agent_files": None,
        }
    ]


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
