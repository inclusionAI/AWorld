from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli import gateway_cli
from aworld_cli.gateway_cli import handle_gateway_channels_list, handle_gateway_status
from aworld_gateway.config import GatewayConfig


def test_gateway_status_reports_default_agent_and_channel_flags(
    tmp_path: Path,
) -> None:
    status = handle_gateway_status(base_dir=tmp_path)

    assert status["default_agent_id"] == "aworld"
    assert status["channels"]["telegram"]["enabled"] is False
    assert status["channels"]["telegram"]["implemented"] is True
    assert status["channels"]["web"]["implemented"] is False


def test_gateway_channels_list_contains_placeholder_channels(
    tmp_path: Path,
) -> None:
    rows = handle_gateway_channels_list(base_dir=tmp_path)

    assert set(rows) >= {"telegram", "web", "dingding", "feishu", "wecom"}
    assert rows["telegram"]["enabled"] is False
    assert rows["telegram"]["implemented"] is True
    assert rows["web"]["implemented"] is False


def test_serve_gateway_bootstraps_runtime_http_app_and_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = GatewayConfig()
    cfg.gateway.host = "0.0.0.0"
    cfg.gateway.port = 18999
    cfg.channels.telegram.enabled = True
    cfg.channels.telegram.default_agent_id = "telegram-agent"
    cfg.channels.telegram.webhook_path = "/hooks/telegram"

    calls: dict[str, object] = {}
    telegram_adapter = object()

    async def fake_load_all_agents(*, remote_backends, local_dirs, agent_files):
        calls["load_all_agents"] = {
            "remote_backends": remote_backends,
            "local_dirs": local_dirs,
            "agent_files": agent_files,
        }
        return []

    class FakeLoader:
        def __init__(self, *, base_dir):
            calls["loader_base_dir"] = base_dir

        def load_or_init(self):
            calls["config_loaded"] = True
            return cfg

    class FakeSessionBinding:
        def __init__(self) -> None:
            calls["session_binding_created"] = True

    class FakeAgentResolver:
        def __init__(self, *, default_agent_id):
            calls["agent_resolver_default_agent_id"] = default_agent_id

    class FakeAgentBackend:
        def __init__(self) -> None:
            calls["agent_backend_created"] = True

    class FakeRouter:
        def __init__(self, *, session_binding, agent_resolver, agent_backend):
            calls["router_args"] = {
                "session_binding": session_binding,
                "agent_resolver": agent_resolver,
                "agent_backend": agent_backend,
            }

    class FakeRegistry:
        def __init__(self) -> None:
            calls["registry_created"] = calls.get("registry_created", 0) + 1

    class FakeRuntime:
        def __init__(self, *, config, registry, router):
            calls["runtime_init"] = {
                "config": config,
                "registry": registry,
                "router": router,
            }

        async def start(self) -> None:
            calls["runtime_started"] = True

        async def stop(self) -> None:
            calls["runtime_stopped"] = True

        def status(self) -> dict[str, object]:
            calls["runtime_status_called"] = True
            return {"state": "running", "channels": {"telegram": {"running": True}}}

        def get_started_channel(self, channel_name: str):
            calls["started_channel_name"] = channel_name
            return telegram_adapter

    class FakeUvicornConfig:
        def __init__(self, *, app, host, port):
            calls["uvicorn_config"] = {
                "app": app,
                "host": host,
                "port": port,
            }

    class FakeUvicornServer:
        def __init__(self, config):
            calls["uvicorn_server_config"] = config

        async def serve(self) -> None:
            calls["uvicorn_serve_called"] = True
            raise RuntimeError("stop after serve")

    def fake_create_gateway_app(
        *,
        runtime_status,
        telegram_adapter,
        telegram_webhook_path,
    ):
        calls["create_gateway_app"] = {
            "runtime_status": runtime_status,
            "telegram_adapter": telegram_adapter,
            "telegram_webhook_path": telegram_webhook_path,
        }
        return "fake-app"

    monkeypatch.setattr("aworld_cli.main.load_all_agents", fake_load_all_agents)
    monkeypatch.setattr(gateway_cli, "GatewayConfigLoader", FakeLoader)
    monkeypatch.setattr(gateway_cli, "SessionBinding", FakeSessionBinding)
    monkeypatch.setattr(gateway_cli, "AgentResolver", FakeAgentResolver)
    monkeypatch.setattr(gateway_cli, "LocalCliAgentBackend", FakeAgentBackend)
    monkeypatch.setattr(gateway_cli, "GatewayRouter", FakeRouter)
    monkeypatch.setattr(gateway_cli, "ChannelRegistry", FakeRegistry)
    monkeypatch.setattr(gateway_cli, "GatewayRuntime", FakeRuntime)
    monkeypatch.setattr(gateway_cli, "create_gateway_app", fake_create_gateway_app)
    monkeypatch.setattr(gateway_cli.uvicorn, "Config", FakeUvicornConfig)
    monkeypatch.setattr(gateway_cli.uvicorn, "Server", FakeUvicornServer)

    with pytest.raises(RuntimeError, match="stop after serve"):
        asyncio.run(
            gateway_cli.serve_gateway(
                base_dir=tmp_path,
                remote_backends=["http://backend"],
                local_dirs=["./agents"],
                agent_files=["./agents/demo.py"],
            )
        )

    assert calls["load_all_agents"] == {
        "remote_backends": ["http://backend"],
        "local_dirs": ["./agents"],
        "agent_files": ["./agents/demo.py"],
    }
    assert calls["loader_base_dir"] == tmp_path
    assert calls["agent_resolver_default_agent_id"] == "aworld"
    assert calls["runtime_started"] is True
    assert calls["create_gateway_app"] == {
        "runtime_status": {
            "state": "running",
            "channels": {"telegram": {"running": True}},
        },
        "telegram_adapter": telegram_adapter,
        "telegram_webhook_path": "/hooks/telegram",
    }
    assert calls["uvicorn_config"]["app"] == "fake-app"
    assert calls["uvicorn_config"]["host"] == "0.0.0.0"
    assert calls["uvicorn_config"]["port"] == 18999
    assert calls["uvicorn_serve_called"] is True
    assert calls["runtime_stopped"] is True
