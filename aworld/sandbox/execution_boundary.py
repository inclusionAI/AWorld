"""Typed description of where a configured sandbox Tool actually executes.

``Sandbox.mode`` is a policy hint used by a few higher-level adapters; it is
not the transport.  A stdio MCP server is a child of the current AWorld
process, while SSE/HTTP/API servers execute across a network boundary.  Keeping
those facts separate prevents a value such as ``mode="local"`` from being
mistaken for proof that every configured Tool is local.

The receipts in this module intentionally omit commands, arguments, endpoint
URLs, environment values, and container identifiers.  They are safe to attach
to diagnostic or trajectory metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
import hashlib
import os

from aworld.sandbox.models import SandboxEnvType


class TransportKind(str, Enum):
    """Transport used to reach a Tool implementation."""

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"
    API = "api"
    FUNCTION_TOOL = "function_tool"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ToolExecutionTarget(str, Enum):
    """Effective execution target, independent from the configured mode label."""

    CURRENT_PROCESS_ENVIRONMENT = "current_process_environment"
    REMOTE_SERVICE = "remote_service"
    DOCKER_CONTAINER = "docker_container"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ToolExecutionBoundaryReceipt:
    """Evidence for one configured MCP server boundary.

    ``working_directory`` is available for in-process policy checks, but is
    deliberately hidden from the dataclass representation.  Only ``to_dict``
    is suitable for trajectory or diagnostic metadata; it emits a one-way
    digest instead of the path.
    """

    SCHEMA_VERSION = "aworld.sandbox.tool-execution-boundary.v1"

    server_name: str
    sandbox_mode: str
    transport: TransportKind
    target: ToolExecutionTarget
    process_relationship: str
    working_directory: str | None = field(repr=False)
    working_directory_source: str | None
    mode_consistent: bool
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        directory_hash = None
        if self.working_directory is not None:
            directory_hash = (
                "sha256:"
                + hashlib.sha256(self.working_directory.encode("utf-8")).hexdigest()
            )
        return {
            "schema_version": self.SCHEMA_VERSION,
            "server_name": self.server_name,
            "sandbox_mode": self.sandbox_mode,
            "transport": self.transport.value,
            "target": self.target.value,
            "process_relationship": self.process_relationship,
            "working_directory_present": self.working_directory is not None,
            "working_directory_hash": directory_hash,
            "working_directory_source": self.working_directory_source,
            "mode_consistent": self.mode_consistent,
            "reason_code": self.reason_code,
        }


def _normalized_mode(value: Any) -> str:
    return str(value or "local").strip().lower()


def _normalized_env_type(value: Any) -> SandboxEnvType | None:
    if isinstance(value, SandboxEnvType):
        return value
    try:
        return SandboxEnvType(value)
    except (TypeError, ValueError):
        return None


def _transport(server_config: Mapping[str, Any] | None) -> TransportKind:
    if not isinstance(server_config, Mapping) or not server_config:
        return TransportKind.UNAVAILABLE
    value = str(server_config.get("type") or "").strip().lower()
    if not value and server_config.get("command"):
        # This is the compatibility behavior used by get_server_instance.
        value = TransportKind.STDIO.value
    try:
        return TransportKind(value)
    except ValueError:
        return TransportKind.UNKNOWN


def _stdio_working_directory(
    server_config: Mapping[str, Any],
    *,
    process_cwd: Path,
) -> tuple[str, str]:
    configured = server_config.get("cwd")
    if configured is None or not str(configured).strip():
        return str(process_cwd.resolve()), "aworld_process_cwd"
    path = Path(str(configured)).expanduser()
    if not path.is_absolute():
        path = process_cwd / path
    return str(path.resolve()), "server_config"


def resolve_tool_execution_boundary(
    *,
    server_name: str,
    server_config: Mapping[str, Any] | None,
    sandbox_mode: str,
    sandbox_env_type: SandboxEnvType | int | None = None,
    sandbox_metadata: Mapping[str, Any] | None = None,
    process_cwd: str | os.PathLike[str] | None = None,
) -> ToolExecutionBoundaryReceipt:
    """Resolve the effective target from the transport and sandbox backend.

    For stdio, an omitted ``cwd`` has the MCP SDK inherit the current AWorld
    process working directory.  Therefore an ``aworld-cli`` running inside a
    Harbor task container spawns the Tool server inside that same container;
    an ``aworld-cli`` running directly on macOS spawns it on macOS.
    """

    mode = _normalized_mode(sandbox_mode)
    transport = _transport(server_config)
    current_cwd = Path(process_cwd or os.getcwd())
    metadata = sandbox_metadata if isinstance(sandbox_metadata, Mapping) else {}
    env_type = _normalized_env_type(sandbox_env_type)

    if transport is TransportKind.UNAVAILABLE:
        return ToolExecutionBoundaryReceipt(
            server_name=server_name,
            sandbox_mode=mode,
            transport=transport,
            target=ToolExecutionTarget.UNAVAILABLE,
            process_relationship="none",
            working_directory=None,
            working_directory_source=None,
            mode_consistent=False,
            reason_code="server_not_configured",
        )

    if transport is TransportKind.FUNCTION_TOOL:
        target = ToolExecutionTarget.CURRENT_PROCESS_ENVIRONMENT
        consistent = mode == "local"
        return ToolExecutionBoundaryReceipt(
            server_name=server_name,
            sandbox_mode=mode,
            transport=transport,
            target=target,
            process_relationship="in_process",
            working_directory=str(current_cwd.resolve()),
            working_directory_source="aworld_process_cwd",
            mode_consistent=consistent,
            reason_code=None if consistent else "mode_transport_mismatch",
        )

    if transport is TransportKind.STDIO:
        server_env = (server_config or {}).get("env")
        is_docker_bridge = (
            env_type is SandboxEnvType.DOCKER
            and server_name == "docker"
            and isinstance(server_env, Mapping)
            and "AWORLD_DOCKER_CONTAINER" in server_env
        )
        if is_docker_bridge:
            docker_workdir = metadata.get("docker_workdir")
            working_directory = (
                str(docker_workdir) if docker_workdir is not None else None
            )
            return ToolExecutionBoundaryReceipt(
                server_name=server_name,
                sandbox_mode=mode,
                transport=transport,
                target=ToolExecutionTarget.DOCKER_CONTAINER,
                process_relationship="stdio_bridge",
                working_directory=working_directory,
                working_directory_source=(
                    "sandbox_metadata" if working_directory is not None else None
                ),
                mode_consistent=mode == "remote",
                reason_code=(
                    None if mode == "remote" else "mode_transport_mismatch"
                ),
            )
        working_directory, directory_source = _stdio_working_directory(
            server_config or {},
            process_cwd=current_cwd,
        )
        consistent = mode == "local"
        return ToolExecutionBoundaryReceipt(
            server_name=server_name,
            sandbox_mode=mode,
            transport=transport,
            target=ToolExecutionTarget.CURRENT_PROCESS_ENVIRONMENT,
            process_relationship="child_process",
            working_directory=working_directory,
            working_directory_source=directory_source,
            mode_consistent=consistent,
            reason_code=None if consistent else "mode_transport_mismatch",
        )

    if transport in {
        TransportKind.SSE,
        TransportKind.STREAMABLE_HTTP,
        TransportKind.API,
    }:
        consistent = mode == "remote"
        return ToolExecutionBoundaryReceipt(
            server_name=server_name,
            sandbox_mode=mode,
            transport=transport,
            target=ToolExecutionTarget.REMOTE_SERVICE,
            process_relationship="network",
            working_directory=None,
            working_directory_source=None,
            mode_consistent=consistent,
            reason_code=None if consistent else "mode_transport_mismatch",
        )

    return ToolExecutionBoundaryReceipt(
        server_name=server_name,
        sandbox_mode=mode,
        transport=transport,
        target=ToolExecutionTarget.UNKNOWN,
        process_relationship="unknown",
        working_directory=None,
        working_directory_source=None,
        mode_consistent=False,
        reason_code="transport_unknown",
    )


__all__ = [
    "ToolExecutionBoundaryReceipt",
    "ToolExecutionTarget",
    "TransportKind",
    "resolve_tool_execution_boundary",
]
