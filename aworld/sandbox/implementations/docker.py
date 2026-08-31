"""Sandbox implementation that attaches AWorld tools to a local Docker container."""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from aworld.sandbox.config.templates import (
    ENV_DOCKER_ALLOWED_DIRECTORIES,
    ENV_DOCKER_BINARY,
    ENV_DOCKER_CONTAINER,
    ENV_DOCKER_SHELL,
    ENV_DOCKER_MAX_OUTPUT_BYTES,
    ENV_DOCKER_OUTPUT_HEAD_BYTES,
    ENV_DOCKER_ARTIFACT_DIRECTORY,
    ENV_DOCKER_WORKDIR,
    build_stdio_server_config,
    get_docker_script_path,
    get_server_env,
)
from aworld.sandbox.implementations.sandbox import Sandbox
from aworld.sandbox.models import SandboxEnvType, SandboxLocalResponse, SandboxStatus


_CONTAINER_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class DockerSandbox(Sandbox):
    """Attach an AWorld sandbox to an already-running local Docker container.

    The container lifecycle remains owned by the caller. ``cleanup()`` only closes
    AWorld's host-side MCP bridge; it never stops or removes the container.
    """

    def __init__(
        self,
        container: str,
        *,
        docker_binary: str = "docker",
        workdir: Optional[str] = None,
        allowed_directories: Optional[List[str]] = None,
        shell: str = "/bin/sh",
        max_inline_output_bytes: int = 1_048_576,
        output_head_bytes: Optional[int] = None,
        artifact_directory: Optional[str] = None,
        validate_connection: bool = True,
        metadata: Optional[Dict[str, str]] = None,
        mcp_config: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        self._validate_container_identifier(container)
        resolved_binary = shutil.which(docker_binary)
        if resolved_binary is None:
            raise RuntimeError(f"Docker executable not found: {docker_binary}")

        inspected_workdir = None
        if validate_connection:
            inspected_workdir = self._inspect_running_container(resolved_binary, container)

        effective_workdir = workdir or inspected_workdir or "/"
        self._validate_absolute_container_path(effective_workdir, "workdir")
        effective_allowed = allowed_directories or [effective_workdir]
        for path in effective_allowed:
            self._validate_absolute_container_path(path, "allowed directory")

        self.container = container
        self.docker_binary = resolved_binary
        self.container_workdir = effective_workdir
        self.allowed_directories = list(effective_allowed)
        self.container_shell = shell
        if max_inline_output_bytes < 1:
            raise ValueError("max_inline_output_bytes must be positive")
        effective_head_bytes = output_head_bytes or max_inline_output_bytes // 2
        if effective_head_bytes < 0 or effective_head_bytes > max_inline_output_bytes:
            raise ValueError("output_head_bytes must be between 0 and max_inline_output_bytes")
        resolved_artifact_directory = None
        if artifact_directory:
            resolved_artifact_directory = str(Path(artifact_directory).expanduser().resolve())
            Path(resolved_artifact_directory).mkdir(parents=True, exist_ok=True)

        bridge_env = get_server_env()
        bridge_env.update(
            {
                ENV_DOCKER_CONTAINER: container,
                ENV_DOCKER_BINARY: resolved_binary,
                ENV_DOCKER_WORKDIR: effective_workdir,
                ENV_DOCKER_ALLOWED_DIRECTORIES: json.dumps(effective_allowed),
                ENV_DOCKER_SHELL: shell,
                ENV_DOCKER_MAX_OUTPUT_BYTES: str(max_inline_output_bytes),
                ENV_DOCKER_OUTPUT_HEAD_BYTES: str(effective_head_bytes),
            }
        )
        if resolved_artifact_directory:
            bridge_env[ENV_DOCKER_ARTIFACT_DIRECTORY] = resolved_artifact_directory
        bridge_config = {
            "mcpServers": {
                "docker": {
                    **build_stdio_server_config(
                        # Use the interpreter that imported AWorld. The generic
                        # placeholder is not resolved on every non-reuse MCP path.
                        command=sys.executable,
                        args=[get_docker_script_path(), "--stdio"],
                        env=bridge_env,
                    ),
                    # Used only by AWorld's logical namespace resolver. Stdio
                    # transports do not send these values to the child process.
                    "headers": {"MCP_SERVERS": "terminal,filesystem"},
                }
            }
        }
        merged_config = self._merge_user_config(mcp_config, bridge_config)
        docker_metadata = dict(metadata or {})
        docker_metadata.update(
            {
                "docker_container": container,
                "docker_workdir": effective_workdir,
                "container_lifecycle": "external",
                "tool_output_policy": {
                    "strategy": "head_tail_artifact",
                    "max_inline_bytes": max_inline_output_bytes,
                    "head_bytes": effective_head_bytes,
                    "artifact_directory": resolved_artifact_directory,
                },
            }
        )

        kwargs.pop("builtin_tools", None)
        kwargs.pop("mode", None)
        kwargs.pop("workspaces", None)
        super().__init__(
            metadata=docker_metadata,
            mcp_config=merged_config,
            mode="remote",
            builtin_tools=None,
            **kwargs,
        )
        self._env_type = SandboxEnvType.DOCKER
        self._metadata.update(docker_metadata)
        self._metadata["env_type"] = SandboxEnvType.DOCKER

    @staticmethod
    def _merge_user_config(user_config: Optional[Any], bridge_config: Dict[str, Any]) -> Dict[str, Any]:
        merged = copy.deepcopy(user_config) if user_config else {"mcpServers": {}}
        servers = merged.setdefault("mcpServers", {})
        if "docker" in servers:
            raise ValueError("mcp_config server name 'docker' is reserved by DockerSandbox")
        servers.update(copy.deepcopy(bridge_config["mcpServers"]))
        return merged

    @staticmethod
    def _validate_container_identifier(container: str) -> None:
        if not container or not _CONTAINER_IDENTIFIER.fullmatch(container):
            raise ValueError(
                "container must be a Docker name or ID containing only letters, "
                "digits, '.', '_' and '-'"
            )

    @staticmethod
    def _validate_absolute_container_path(path: str, label: str) -> None:
        if not path or not PurePosixPath(path).is_absolute():
            raise ValueError(f"{label} must be an absolute container path: {path!r}")

    @staticmethod
    def _inspect_running_container(docker_binary: str, container: str) -> str:
        result = subprocess.run(
            [
                docker_binary,
                "inspect",
                "--format",
                "{{json .State.Running}}\t{{json .Config.WorkingDir}}",
                container,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "container not found"
            raise RuntimeError(f"Unable to inspect Docker container {container!r}: {detail}")
        try:
            running_json, workdir_json = result.stdout.strip().split("\t", 1)
            running = json.loads(running_json)
            workdir = json.loads(workdir_json)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unexpected docker inspect output for {container!r}") from exc
        if running is not True:
            raise RuntimeError(f"Docker container {container!r} is not running")
        return workdir or "/"

    @classmethod
    def _create_sandbox(cls, **kwargs: Any) -> SandboxLocalResponse:
        return SandboxLocalResponse(
            sandbox_id=kwargs.get("sandbox_id"),
            status=SandboxStatus.RUNNING,
            mcp_config=kwargs.get("mcp_config"),
            env_type=SandboxEnvType.DOCKER,
            skill_configs=kwargs.get("skill_configs"),
        )

    async def remove(self) -> bool:
        """Detach AWorld without changing the externally-owned container."""
        return True
