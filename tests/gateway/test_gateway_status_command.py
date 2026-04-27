from __future__ import annotations

import asyncio
import importlib
import os
import sys
from types import ModuleType
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
    assert status["channels"]["dingding"]["enabled"] is False
    assert status["channels"]["dingding"]["implemented"] is True
    assert status["channels"]["wechat"]["enabled"] is False
    assert status["channels"]["wechat"]["implemented"] is True
    assert status["channels"]["wecom"]["enabled"] is False
    assert status["channels"]["wecom"]["implemented"] is True
    assert status["channels"]["web"]["implemented"] is False


def test_gateway_channels_list_contains_placeholder_channels(
    tmp_path: Path,
) -> None:
    rows = handle_gateway_channels_list(base_dir=tmp_path)

    assert set(rows) >= {"telegram", "web", "dingding", "wechat", "feishu", "wecom"}
    assert rows["telegram"]["enabled"] is False
    assert rows["telegram"]["implemented"] is True
    assert rows["dingding"]["enabled"] is False
    assert rows["dingding"]["implemented"] is True
    assert rows["wechat"]["enabled"] is False
    assert rows["wechat"]["implemented"] is True
    assert rows["wecom"]["enabled"] is False
    assert rows["wecom"]["implemented"] is True
    assert rows["web"]["implemented"] is False


def test_enable_aworld_console_logging_for_gateway_reconfigures_disabled_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeLogger:
        def __init__(
            self,
            *,
            tag: str = "aworld",
            name: str = "AWorld",
            console_level: str = "INFO",
            formatter=None,
            disable_console: bool = True,
            file_log_config=None,
        ) -> None:
            self.tag = tag
            self.name = name
            self.console_level = console_level
            self.formater = formatter
            self.file_log_config = file_log_config or {"rotation": "32 MB"}
            self.disable_console = disable_console
            self.calls = getattr(self, "calls", [])
            self.calls.append(
                {
                    "tag": tag,
                    "name": name,
                    "console_level": console_level,
                    "disable_console": disable_console,
                }
            )

    import aworld.logs.util as log_util

    fake_logger = FakeLogger(disable_console=True)
    monkeypatch.setattr(log_util, "logger", fake_logger)
    monkeypatch.setenv("AWORLD_DISABLE_CONSOLE_LOG", "true")

    gateway_cli._enable_aworld_console_logging_for_gateway()

    assert os.environ["AWORLD_DISABLE_CONSOLE_LOG"] == "false"
    assert fake_logger.disable_console is False
    assert fake_logger.calls[-1]["disable_console"] is False


def test_serve_gateway_enables_console_logging_before_loading_agents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, str] = {}
    fake_main = ModuleType("aworld_cli.main")

    async def fake_load_all_agents(*, remote_backends, local_dirs, agent_files):
        observed["disable_console_env"] = os.environ.get("AWORLD_DISABLE_CONSOLE_LOG", "")
        observed["quiet_boot_env"] = os.environ.get("AWORLD_GATEWAY_QUIET_BOOT", "")
        raise RuntimeError("stop after env check")

    fake_main.load_all_agents = fake_load_all_agents  # type: ignore[attr-defined]

    monkeypatch.setenv("AWORLD_DISABLE_CONSOLE_LOG", "true")
    monkeypatch.setitem(sys.modules, "aworld_cli.main", fake_main)

    with pytest.raises(RuntimeError, match="stop after env check"):
        asyncio.run(
            gateway_cli.serve_gateway(
                base_dir=tmp_path,
                remote_backends=[],
                local_dirs=[],
                agent_files=[],
            )
        )

    assert observed["disable_console_env"] == "false"
    assert observed["quiet_boot_env"] == "true"


def test_boot_logging_downgrades_verbose_info_to_debug_in_quiet_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boot_logging = importlib.import_module("aworld_cli.core.boot_logging")
    calls: list[tuple[str, str]] = []

    class FakeLogger:
        def info(self, message: str) -> None:
            calls.append(("info", message))

        def debug(self, message: str) -> None:
            calls.append(("debug", message))

    monkeypatch.setenv("AWORLD_GATEWAY_QUIET_BOOT", "true")

    boot_logging.log_verbose_boot(FakeLogger(), "loading module", level="info")

    assert calls == [("debug", "loading module")]


def test_boot_logging_downgrades_verbose_warning_to_debug_in_quiet_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boot_logging = importlib.import_module("aworld_cli.core.boot_logging")
    calls: list[tuple[str, str]] = []

    class FakeLogger:
        def warning(self, message: str) -> None:
            calls.append(("warning", message))

        def debug(self, message: str) -> None:
            calls.append(("debug", message))

    monkeypatch.setenv("AWORLD_GATEWAY_QUIET_BOOT", "true")

    boot_logging.log_verbose_boot(FakeLogger(), "duplicate agent", level="warning")

    assert calls == [("debug", "duplicate agent")]


def test_build_artifact_service_defaults_public_base_url_from_host_ip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = GatewayConfig()
    cfg.gateway.port = 18080
    cfg.gateway.public_base_url = None
    cfg.channels.dingding.enabled = True
    cfg.channels.dingding.workspace_dir = None

    monkeypatch.setattr(gateway_cli, "_detect_gateway_host_ip", lambda: "10.20.30.40")

    artifact_service = gateway_cli._build_artifact_service(base_dir=tmp_path, config=cfg)

    assert artifact_service is not None
    assert artifact_service._public_base_url == "http://10.20.30.40:18080"
    expected_workspace_path = (
        tmp_path / ".aworld" / "gateway" / "dingding"
    ).resolve()
    assert artifact_service._allowed_roots == [expected_workspace_path]
    assert cfg.channels.dingding.workspace_dir == str(expected_workspace_path)


def test_serve_gateway_bootstraps_runtime_http_app_and_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = GatewayConfig()
    cfg.gateway.host = "0.0.0.0"
    cfg.gateway.port = 18999
    cfg.gateway.public_base_url = "https://gateway.example.com"
    cfg.channels.telegram.enabled = True
    cfg.channels.dingding.enabled = True
    cfg.channels.telegram.default_agent_id = "telegram-agent"
    cfg.channels.telegram.webhook_path = "/hooks/telegram"
    cfg.channels.dingding.workspace_dir = None

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
        def __init__(self, *, config, registry, router, artifact_service):
            calls["runtime_init"] = {
                "config": config,
                "registry": registry,
                "router": router,
                "artifact_service": artifact_service,
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
        artifact_service,
    ):
        calls["create_gateway_app"] = {
            "runtime_status": runtime_status,
            "telegram_adapter": telegram_adapter,
            "telegram_webhook_path": telegram_webhook_path,
            "artifact_service": artifact_service,
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
    expected_workspace_path = (
        tmp_path / ".aworld" / "gateway" / "dingding"
    ).resolve()
    runtime_artifact_service = calls["runtime_init"]["artifact_service"]
    app_artifact_service = calls["create_gateway_app"]["artifact_service"]
    assert runtime_artifact_service is app_artifact_service
    assert runtime_artifact_service._allowed_roots == [expected_workspace_path]
    assert cfg.channels.dingding.workspace_dir == str(expected_workspace_path)
    assert calls["create_gateway_app"] == {
        "runtime_status": {
            "state": "running",
            "channels": {"telegram": {"running": True}},
        },
        "telegram_adapter": telegram_adapter,
        "telegram_webhook_path": "/hooks/telegram",
        "artifact_service": app_artifact_service,
    }
    assert calls["uvicorn_config"]["app"] == "fake-app"
    assert calls["uvicorn_config"]["host"] == "0.0.0.0"
    assert calls["uvicorn_config"]["port"] == 18999
    assert calls["uvicorn_serve_called"] is True
    assert calls["runtime_stopped"] is True


def test_serve_gateway_skips_artifact_service_when_dingding_disabled_and_workspace_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = GatewayConfig()
    cfg.channels.dingding.enabled = False
    bad_workspace = tmp_path / "bad-workspace"
    bad_workspace.write_text("not-a-dir", encoding="utf-8")
    cfg.channels.dingding.workspace_dir = str(bad_workspace)

    calls: dict[str, object] = {}

    async def fake_load_all_agents(*, remote_backends, local_dirs, agent_files):
        return []

    class FakeLoader:
        def __init__(self, *, base_dir):
            calls["loader_base_dir"] = base_dir

        def load_or_init(self):
            return cfg

    class FakeRuntime:
        def __init__(self, *, config, registry, router, artifact_service):
            calls["runtime_artifact_service"] = artifact_service

        async def start(self) -> None:
            calls["runtime_started"] = True

        async def stop(self) -> None:
            calls["runtime_stopped"] = True

        def status(self) -> dict[str, object]:
            return {"state": "running", "channels": {}}

        def get_started_channel(self, channel_name: str):
            return None

    class FakeUvicornConfig:
        def __init__(self, *, app, host, port):
            calls["uvicorn_app"] = app

    class FakeUvicornServer:
        def __init__(self, config):
            return None

        async def serve(self) -> None:
            raise RuntimeError("stop after serve")

    def fake_create_gateway_app(
        *,
        runtime_status,
        telegram_adapter,
        telegram_webhook_path,
        artifact_service,
    ):
        calls["app_artifact_service"] = artifact_service
        return "fake-app"

    monkeypatch.setattr("aworld_cli.main.load_all_agents", fake_load_all_agents)
    monkeypatch.setattr(gateway_cli, "GatewayConfigLoader", FakeLoader)
    monkeypatch.setattr(gateway_cli, "GatewayRuntime", FakeRuntime)
    monkeypatch.setattr(gateway_cli, "create_gateway_app", fake_create_gateway_app)
    monkeypatch.setattr(gateway_cli.uvicorn, "Config", FakeUvicornConfig)
    monkeypatch.setattr(gateway_cli.uvicorn, "Server", FakeUvicornServer)

    with pytest.raises(RuntimeError, match="stop after serve"):
        asyncio.run(
            gateway_cli.serve_gateway(
                base_dir=tmp_path,
                remote_backends=[],
                local_dirs=[],
                agent_files=[],
            )
        )

    assert calls["runtime_started"] is True
    assert calls["runtime_artifact_service"] is None
    assert calls["app_artifact_service"] is None


def test_resolve_dingding_workspace_dir_uses_default_for_whitespace(tmp_path: Path) -> None:
    cfg = GatewayConfig()
    cfg.channels.dingding.workspace_dir = "   "

    resolved = gateway_cli._resolve_dingding_workspace_dir(base_dir=tmp_path, config=cfg)
    expected = (tmp_path / ".aworld" / "gateway" / "dingding").resolve()

    assert resolved == expected
    assert cfg.channels.dingding.workspace_dir == str(expected)


def test_build_artifact_service_returns_none_for_enabled_unusable_workspace(
    tmp_path: Path,
) -> None:
    cfg = GatewayConfig()
    cfg.channels.dingding.enabled = True
    bad_workspace = tmp_path / "bad-workspace"
    bad_workspace.write_text("not-a-dir", encoding="utf-8")
    cfg.channels.dingding.workspace_dir = str(bad_workspace)

    artifact_service = gateway_cli._build_artifact_service(base_dir=tmp_path, config=cfg)

    assert artifact_service is None
