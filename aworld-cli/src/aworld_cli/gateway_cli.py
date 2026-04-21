from __future__ import annotations

import argparse
import asyncio
import os
import socket
from pathlib import Path
from typing import Sequence

import uvicorn

from aworld_cli.core.boot_logging import enable_quiet_gateway_boot
from aworld_gateway.agent_resolver import AgentResolver
from aworld_gateway.config import GatewayConfigLoader
from aworld_gateway.config import GatewayConfig
from aworld_gateway.http.artifact_service import ArtifactService
from aworld_gateway.http.server import create_gateway_app
from aworld_gateway.registry import ChannelRegistry
from aworld_gateway.router import GatewayRouter, LocalCliAgentBackend
from aworld_gateway.runtime import GatewayRuntime
from aworld_gateway.session_binding import SessionBinding

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

    subparsers.add_parser("server", help="Start the gateway service")
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


def _load_gateway_config_read_only(base_dir: Path | str | None = None) -> GatewayConfig:
    resolved_base_dir = Path.cwd() if base_dir is None else Path(base_dir)
    loader = GatewayConfigLoader(base_dir=resolved_base_dir)
    if loader.config_path.exists():
        return loader.load_or_init()
    return GatewayConfig()


def _resolve_dingding_workspace_dir(*, base_dir: Path, config: GatewayConfig) -> Path:
    configured_workspace_dir = config.channels.dingding.workspace_dir
    configured_workspace_dir = (
        configured_workspace_dir.strip() if configured_workspace_dir is not None else None
    )
    if configured_workspace_dir:
        workspace_dir = Path(configured_workspace_dir).expanduser()
        if not workspace_dir.is_absolute():
            workspace_dir = base_dir / workspace_dir
    else:
        workspace_dir = base_dir / ".aworld" / "gateway" / "dingding"

    resolved_workspace_dir = workspace_dir.resolve()
    resolved_workspace_dir.mkdir(parents=True, exist_ok=True)
    config.channels.dingding.workspace_dir = str(resolved_workspace_dir)
    return resolved_workspace_dir


def _detect_gateway_host_ip() -> str | None:
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp_socket.connect(("8.8.8.8", 80))
        detected_ip = udp_socket.getsockname()[0].strip()
        if detected_ip and detected_ip != "0.0.0.0":
            return detected_ip
    except OSError:
        pass
    finally:
        udp_socket.close()

    try:
        detected_ip = socket.gethostbyname(socket.gethostname()).strip()
    except OSError:
        return None
    if not detected_ip or detected_ip in {"0.0.0.0", "127.0.0.1"}:
        return None
    return detected_ip


def _resolve_gateway_public_base_url(config: GatewayConfig) -> str | None:
    configured_public_base_url = (
        str(config.gateway.public_base_url).strip()
        if config.gateway.public_base_url is not None
        else ""
    )
    if configured_public_base_url:
        return configured_public_base_url

    detected_ip = _detect_gateway_host_ip()
    if not detected_ip:
        return None
    return f"http://{detected_ip}:{config.gateway.port}"


def _build_artifact_service(
    *,
    base_dir: Path,
    config: GatewayConfig,
) -> ArtifactService | None:
    if not config.channels.dingding.enabled:
        return None
    try:
        workspace_dir = _resolve_dingding_workspace_dir(base_dir=base_dir, config=config)
    except OSError:
        return None
    return ArtifactService(
        public_base_url=_resolve_gateway_public_base_url(config),
        allowed_roots=[workspace_dir],
    )


def _enable_aworld_console_logging_for_gateway() -> None:
    os.environ["AWORLD_DISABLE_CONSOLE_LOG"] = "false"

    try:
        from aworld.logs import util as log_util
    except Exception:
        return

    aworld_logger = getattr(log_util, "logger", None)
    if aworld_logger is None or not getattr(aworld_logger, "disable_console", False):
        return

    file_log_config = getattr(aworld_logger, "file_log_config", None)
    if isinstance(file_log_config, dict):
        file_log_config = dict(file_log_config)

    aworld_logger.__init__(
        tag=getattr(aworld_logger, "tag", "aworld"),
        name=getattr(aworld_logger, "name", "AWorld"),
        console_level=getattr(aworld_logger, "console_level", "INFO"),
        formatter=getattr(aworld_logger, "formater", None),
        disable_console=False,
        file_log_config=file_log_config,
    )


def handle_gateway_status(base_dir: Path | str | None = None) -> dict[str, object]:
    config = _load_gateway_config_read_only(base_dir)
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )
    runtime_status = runtime.status()
    return {
        "default_agent_id": config.default_agent_id,
        "state": runtime_status["state"],
        "channels": runtime_status["channels"],
    }


def handle_gateway_channels_list(
    base_dir: Path | str | None = None,
) -> dict[str, dict[str, object]]:
    config = _load_gateway_config_read_only(base_dir)
    channel_meta = ChannelRegistry().list_channels()
    return {
        channel_id: {
            "label": meta["label"],
            "enabled": getattr(config.channels, channel_id).enabled,
            "implemented": meta["implemented"],
        }
        for channel_id, meta in channel_meta.items()
    }


async def serve_gateway(
    *,
    base_dir: Path | str | None,
    remote_backends: list[str] | None,
    local_dirs: list[str] | None,
    agent_files: list[str] | None,
) -> None:
    _enable_aworld_console_logging_for_gateway()
    enable_quiet_gateway_boot()

    from aworld_cli.main import load_all_agents

    await load_all_agents(
        remote_backends=remote_backends,
        local_dirs=local_dirs,
        agent_files=agent_files,
    )

    resolved_base_dir = Path.cwd() if base_dir is None else Path(base_dir)
    config = GatewayConfigLoader(base_dir=resolved_base_dir).load_or_init()
    artifact_service = _build_artifact_service(base_dir=resolved_base_dir, config=config)
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id=config.default_agent_id),
        agent_backend=LocalCliAgentBackend(),
    )
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=router,
        artifact_service=artifact_service,
    )

    await runtime.start()
    telegram_adapter = runtime.get_started_channel("telegram")
    app = create_gateway_app(
        runtime_status=runtime.status(),
        artifact_service=artifact_service,
        telegram_adapter=telegram_adapter,
        telegram_webhook_path=config.channels.telegram.webhook_path,
    )
    uvicorn_config = uvicorn.Config(
        app=app,
        host=config.gateway.host,
        port=config.gateway.port,
    )
    server = uvicorn.Server(uvicorn_config)

    try:
        await server.serve()
    finally:
        await runtime.stop()
