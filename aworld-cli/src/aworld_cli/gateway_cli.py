from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Sequence

import uvicorn

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
        public_base_url=config.gateway.public_base_url,
        allowed_roots=[workspace_dir],
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
