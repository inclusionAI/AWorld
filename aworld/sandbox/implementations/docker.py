"""Sandbox implementation that attaches AWorld tools to a local Docker container."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
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
from aworld.core.tool_action_journal import (
    append_tool_action_event,
    tool_action_batch_id,
)


_CONTAINER_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ENV_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MUTATING_FILE_ACTIONS = {
    "create_directory",
    "edit_file",
    "move_file",
    "upload_file",
    "write_file",
    "write_file_base64",
}
_MUTATING_SHELL_PATTERN = re.compile(
    r"(?:^|[;&|\n]\s*|\bsudo\s+)(?:rm|mv|cp|install|mkdir|touch|truncate|dd)\b"
    r"|\bsed\s+[^\n;|]*\s-i(?:\s|$)"
    r"|\bgit\s+(?:checkout|restore|reset|clean)\b"
    r"|(?:^|[^<])(?:>>?|2>>?)\s*[^&]",
    re.IGNORECASE,
)
_OPAQUE_SHELL_ACTIONS = {"run_code", "execute_command", "mcp_execute_command"}


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
        destructive_checkpoint: bool = False,
        tracked_artifact_paths: Optional[List[str]] = None,
        checkpoint_directory: Optional[str] = None,
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
        self.destructive_checkpoint = bool(destructive_checkpoint)
        tracked_paths = tracked_artifact_paths or [effective_workdir]
        resolved_tracked_paths: list[str] = []
        for path in tracked_paths:
            candidate = PurePosixPath(path)
            if not candidate.is_absolute():
                candidate = PurePosixPath(effective_workdir) / candidate
            normalized = str(candidate)
            self._validate_absolute_container_path(normalized, "tracked artifact path")
            if normalized == "/":
                if self.destructive_checkpoint:
                    raise ValueError(
                        "destructive checkpoint requires a tracked path narrower than '/'"
                    )
                continue
            if normalized not in resolved_tracked_paths:
                resolved_tracked_paths.append(normalized)
        self.tracked_artifact_paths = resolved_tracked_paths
        self._checkpoint_lock = asyncio.Lock()
        self._checkpoint_files: set[Path] = set()
        self._last_artifact_fingerprint: str | None = None
        resolved_checkpoint_directory = None
        if checkpoint_directory:
            resolved_checkpoint_directory = Path(checkpoint_directory).expanduser().resolve()
            resolved_checkpoint_directory.mkdir(parents=True, exist_ok=True)
        elif self.destructive_checkpoint:
            resolved_checkpoint_directory = Path(artifact_directory or ".").expanduser().resolve() / "sandbox-checkpoints"
            resolved_checkpoint_directory.mkdir(parents=True, exist_ok=True)
        self.checkpoint_directory = resolved_checkpoint_directory
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
                "destructive_checkpoint": {
                    "enabled": self.destructive_checkpoint,
                    "tracked_path_count": len(self.tracked_artifact_paths),
                    "transaction_scope": "all_shell_and_declared_file_writes",
                    "rollback_policy": (
                        "failed_action_or_unexpected_implicit_artifact_loss"
                    ),
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
    def _action_value(action: Any, name: str, default: Any = None) -> Any:
        if isinstance(action, dict):
            return action.get(name, default)
        return getattr(action, name, default)

    @classmethod
    def _is_mutating_action(cls, action: Any) -> bool:
        action_name = str(cls._action_value(action, "action_name", "") or "")
        if action_name in _MUTATING_FILE_ACTIONS:
            return True
        if action_name not in _OPAQUE_SHELL_ACTIONS:
            return False
        params = cls._action_value(action, "params", {}) or {}
        if not isinstance(params, dict):
            return False
        command = params.get("code") or params.get("command") or ""
        return isinstance(command, str) and bool(_MUTATING_SHELL_PATTERN.search(command))

    @classmethod
    def _requires_transaction(cls, action: Any) -> bool:
        """Snapshot opaque shell execution without guessing executable behavior."""
        action_name = str(cls._action_value(action, "action_name", "") or "")
        return action_name in _OPAQUE_SHELL_ACTIONS or action_name in _MUTATING_FILE_ACTIONS

    def _docker_run(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.run(command, check=False, **kwargs)

    @staticmethod
    def _sha256_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _artifact_fingerprint_sync(self) -> str | None:
        if not self.destructive_checkpoint or not self.tracked_artifact_paths:
            return None
        program = (
            'for target do if [ -e "$target" ]; then '
            'find "$target" -type d -print; '
            'find "$target" -type f -exec cksum {} \\; -print; '
            'find "$target" -type l -exec readlink {} \\; -print; '
            'else printf "missing:%s\\n" "$target"; fi; done | LC_ALL=C sort'
        )
        result = self._docker_run(
            [
                self.docker_binary,
                "exec",
                self.container,
                self.container_shell,
                "-c",
                program,
                "aworld-artifact-fingerprint",
                *self.tracked_artifact_paths,
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            return None
        output = result.stdout or b""
        if isinstance(output, str):
            output = output.encode()
        return hashlib.sha256(output).hexdigest()

    def _artifact_paths_sync(self) -> frozenset[str] | None:
        """List tracked paths with NUL framing for loss detection.

        Content identity remains owned by ``_artifact_fingerprint_sync``.  This
        inventory is used only to detect that a previously present path vanished;
        paths are never exposed in model-visible receipts.
        """
        if not self.destructive_checkpoint or not self.tracked_artifact_paths:
            return None
        program = (
            'for target do if [ -e "$target" ] || [ -L "$target" ]; then '
            'find "$target" -print0; '
            'else printf "missing:%s\\0" "$target"; fi; done'
        )
        result = self._docker_run(
            [
                self.docker_binary,
                "exec",
                self.container,
                self.container_shell,
                "-c",
                program,
                "aworld-artifact-inventory",
                *self.tracked_artifact_paths,
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            return None
        output = result.stdout or b""
        if isinstance(output, str):
            output = output.encode()
        return frozenset(
            item.decode("utf-8", errors="surrogateescape")
            for item in output.split(b"\0")
            if item
        )

    def _create_checkpoint_sync(self) -> dict[str, Any]:
        if self.checkpoint_directory is None:
            raise RuntimeError("checkpoint directory is not configured")
        checkpoint_id = uuid.uuid4().hex
        archive = self.checkpoint_directory / f"{checkpoint_id}.tar"
        existing: list[str] = []
        for path in self.tracked_artifact_paths:
            found = self._docker_run(
                [self.docker_binary, "exec", self.container, "test", "-e", path],
                capture_output=True,
                timeout=30,
            )
            if found.returncode == 0:
                existing.append(path.lstrip("/"))
        with archive.open("wb") as stream:
            command = [self.docker_binary, "exec", self.container, "tar", "-C", "/", "-cf", "-"]
            command.extend(existing if existing else ["--files-from", "/dev/null"])
            snapshot = self._docker_run(
                command,
                stdout=stream,
                stderr=subprocess.PIPE,
                timeout=300,
            )
        if snapshot.returncode != 0:
            archive.unlink(missing_ok=True)
            detail = (snapshot.stderr or b"").decode(errors="replace")[:500]
            raise RuntimeError(f"Docker checkpoint failed: {detail}")
        self._checkpoint_files.add(archive)
        return {
            "id": checkpoint_id,
            "archive": archive,
            "sha256": self._sha256_path(archive),
            "existing_paths": tuple(f"/{path}" for path in existing),
        }

    def _restore_checkpoint_sync(self, checkpoint: dict[str, Any]) -> None:
        archive = Path(checkpoint["archive"])
        if self._sha256_path(archive) != checkpoint["sha256"]:
            raise RuntimeError("Docker checkpoint checksum mismatch")
        cleanup_program = (
            'for target do if [ -d "$target" ]; then '
            'find "$target" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; '
            'else rm -f -- "$target"; fi; done'
        )
        cleared = self._docker_run(
            [
                self.docker_binary,
                "exec",
                self.container,
                self.container_shell,
                "-c",
                cleanup_program,
                "aworld-checkpoint-rollback",
                *self.tracked_artifact_paths,
            ],
            capture_output=True,
            timeout=120,
        )
        if cleared.returncode != 0:
            detail = (cleared.stderr or b"")
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            raise RuntimeError(
                f"Unable to clear tracked paths before rollback: {str(detail)[:500]}"
            )
        with archive.open("rb") as stream:
            restored = self._docker_run(
                [self.docker_binary, "exec", "-i", self.container, "tar", "-C", "/", "-xf", "-"],
                stdin=stream,
                capture_output=True,
                timeout=300,
            )
        if restored.returncode != 0:
            detail = (restored.stderr or b"")
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            raise RuntimeError(f"Unable to restore Docker checkpoint: {str(detail)[:500]}")
        existing_paths = set(checkpoint.get("existing_paths") or ())
        missing_paths = [
            path for path in self.tracked_artifact_paths if path not in existing_paths
        ]
        if missing_paths:
            removed = self._docker_run(
                [
                    self.docker_binary,
                    "exec",
                    self.container,
                    self.container_shell,
                    "-c",
                    'for target do rm -rf -- "$target"; done',
                    "aworld-remove-created-artifacts",
                    *missing_paths,
                ],
                capture_output=True,
                timeout=120,
            )
            if removed.returncode != 0:
                raise RuntimeError("Unable to remove artifacts absent at checkpoint")

    def _discard_checkpoint(self, checkpoint: dict[str, Any] | None) -> None:
        if not checkpoint:
            return
        archive = Path(checkpoint["archive"])
        archive.unlink(missing_ok=True)
        self._checkpoint_files.discard(archive)

    @staticmethod
    def _results_failed(results: list[Any]) -> bool:
        for result in results:
            success = (
                result.get("success", True)
                if isinstance(result, dict)
                else getattr(result, "success", True)
            )
            if success is False:
                return True
            content = (
                result.get("content")
                if isinstance(result, dict)
                else getattr(result, "content", None)
            )
            if isinstance(content, str) and content.lstrip().startswith("{"):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    content = None
            if isinstance(content, dict):
                if content.get("success") is False:
                    return True
                metadata = content.get("metadata")
                if isinstance(metadata, dict) and (
                    metadata.get("timed_out") is True
                    or (
                        isinstance(metadata.get("return_code"), int)
                        and metadata["return_code"] != 0
                    )
                ):
                    return True
        return False

    @staticmethod
    def _attach_context_receipt(results: list[Any], receipt: dict[str, Any]) -> None:
        for result in results:
            if isinstance(result, dict):
                metadata = dict(result.get("metadata") or {})
                metadata["context_management"] = dict(receipt)
                result["metadata"] = metadata
            else:
                metadata = dict(getattr(result, "metadata", None) or {})
                metadata["context_management"] = dict(receipt)
                result.metadata = metadata

    @staticmethod
    def _mark_unexpected_rollback(results: list[Any]) -> None:
        error = "sandbox transaction rolled back unexpected implicit artifact loss"
        for result in results:
            if isinstance(result, dict):
                result["success"] = False
                result["error"] = error
            else:
                result.success = False
                result.error = error

    @staticmethod
    def _journal_transaction(
        context: Any,
        actions: list[Any],
        receipt: dict[str, Any],
    ) -> None:
        if context is None:
            return
        try:
            append_tool_action_event(
                context=context,
                event_type="sandbox_transaction_resolved",
                actions=actions,
                status=(
                    "rolled_back"
                    if receipt["rollback_performed"]
                    else "failed"
                    if receipt.get("tool_failed") is True
                    else "committed"
                ),
                batch_id=tool_action_batch_id(actions),
                metadata={"context_management": receipt},
            )
        except Exception as exc:
            from aworld.logs.util import logger

            logger.warning(
                "Sandbox transaction journal append failed open; "
                f"error_type={type(exc).__name__}"
            )

    async def call_tool(
        self,
        action_list: List[Dict[str, Any]] = None,
        task_id: str = None,
        session_id: str = None,
        context: Any = None,
        event_message: Any = None,
    ) -> List[Any]:
        actions = action_list or []
        if not self.destructive_checkpoint or not self.tracked_artifact_paths:
            return await super().call_tool(
                actions, task_id, session_id, context, event_message
            )
        async with self._checkpoint_lock:
            before = await asyncio.to_thread(self._artifact_fingerprint_sync)
            before_paths = await asyncio.to_thread(self._artifact_paths_sync)
            declared_mutating = any(self._is_mutating_action(action) for action in actions)
            transactional = any(self._requires_transaction(action) for action in actions)
            checkpoint = (
                await asyncio.to_thread(self._create_checkpoint_sync)
                if transactional
                else None
            )
            rolled_back = False
            rollback_attempted = False
            rollback_reason = None
            rollback_skipped_reason = None
            try:
                results = await super().call_tool(
                    actions, task_id, session_id, context, event_message
                )
                if results is None:
                    results = []
                tool_failed = self._results_failed(results)
                observed_after = await asyncio.to_thread(
                    self._artifact_fingerprint_sync
                )
                observed_after_paths = await asyncio.to_thread(
                    self._artifact_paths_sync
                )
                removed_paths = (
                    frozenset(
                        path
                        for path in before_paths - observed_after_paths
                        if not path.startswith("missing:")
                    )
                    if before_paths is not None and observed_after_paths is not None
                    else frozenset()
                )
                unexpected_loss = bool(
                    checkpoint is not None
                    and removed_paths
                    and not declared_mutating
                )
                if checkpoint is not None and tool_failed and before != observed_after:
                    rollback_attempted = True
                    await asyncio.to_thread(self._restore_checkpoint_sync, checkpoint)
                    rolled_back = True
                    rollback_reason = "tool_failure"
                elif checkpoint is not None and tool_failed:
                    rollback_skipped_reason = "artifact_unchanged_after_tool_failure"
                elif unexpected_loss:
                    rollback_attempted = True
                    await asyncio.to_thread(self._restore_checkpoint_sync, checkpoint)
                    rolled_back = True
                    rollback_reason = "unexpected_implicit_artifact_loss"
                    self._mark_unexpected_rollback(results)
                after = (
                    await asyncio.to_thread(self._artifact_fingerprint_sync)
                    if rolled_back
                    else observed_after
                )
                removed_paths = (
                    frozenset(
                        path
                        for path in before_paths - observed_after_paths
                        if not path.startswith("missing:")
                    )
                    if before_paths is not None and observed_after_paths is not None
                    else frozenset()
                )
                added_paths = (
                    frozenset(
                        path
                        for path in observed_after_paths - before_paths
                        if not path.startswith("missing:")
                    )
                    if before_paths is not None and observed_after_paths is not None
                    else frozenset()
                )
                receipt = {
                    "schema_version": "aworld.sandbox-artifact-progress/v1",
                    "artifact_fingerprint_before": before,
                    "artifact_fingerprint_after": after,
                    "artifact_changed": bool(before != after),
                    "artifact_change_observed_before_resolution": bool(
                        observed_after is not None and before != observed_after
                    ),
                    "artifact_fingerprint_observed_after_action": observed_after,
                    "artifact_inventory_available": bool(
                        before_paths is not None and observed_after_paths is not None
                    ),
                    "added_artifact_count": len(added_paths),
                    "removed_artifact_count": len(removed_paths),
                    "implicit_artifact_loss_detected": bool(
                        removed_paths and not declared_mutating
                    ),
                    "tool_failed": tool_failed,
                    "mutating_action": declared_mutating,
                    "transactional_action": transactional,
                    "checkpoint_created": checkpoint is not None,
                    "checkpoint_id": checkpoint.get("id") if checkpoint else None,
                    "checkpoint_sha256": checkpoint.get("sha256") if checkpoint else None,
                    "rollback_performed": rolled_back,
                    "rollback_reason": rollback_reason,
                    "rollback_skipped_reason": rollback_skipped_reason,
                }
                self._attach_context_receipt(results, receipt)
                self._journal_transaction(context, actions, receipt)
                self._last_artifact_fingerprint = after
                return results
            except BaseException:
                if checkpoint is not None and not rollback_attempted:
                    await asyncio.to_thread(self._restore_checkpoint_sync, checkpoint)
                    rollback_attempted = True
                    rolled_back = True
                if checkpoint is not None:
                    after = (
                        await asyncio.to_thread(self._artifact_fingerprint_sync)
                        if rolled_back
                        else None
                    )
                    receipt = {
                        "schema_version": "aworld.sandbox-artifact-progress/v1",
                        "artifact_fingerprint_before": before,
                        "artifact_fingerprint_after": after,
                        "artifact_changed": bool(
                            before is not None and after is not None and before != after
                        ),
                        "artifact_change_observed_before_resolution": None,
                        "artifact_fingerprint_observed_after_action": None,
                        "artifact_inventory_available": before_paths is not None,
                        "added_artifact_count": None,
                        "removed_artifact_count": None,
                        "implicit_artifact_loss_detected": None,
                        "tool_failed": True,
                        "mutating_action": declared_mutating,
                        "transactional_action": transactional,
                        "checkpoint_created": True,
                        "checkpoint_id": checkpoint.get("id"),
                        "checkpoint_sha256": checkpoint.get("sha256"),
                        "rollback_performed": rolled_back,
                        "rollback_reason": "tool_exception" if rolled_back else None,
                        "rollback_skipped_reason": None,
                    }
                    self._journal_transaction(context, actions, receipt)
                raise
            finally:
                self._discard_checkpoint(checkpoint)

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

    async def cleanup(self) -> None:
        try:
            await super().cleanup()
        finally:
            for archive in tuple(self._checkpoint_files):
                archive.unlink(missing_ok=True)
                self._checkpoint_files.discard(archive)

    async def run_validation(
        self,
        argv: tuple[str, ...] | list[str],
        *,
        cwd: str | None = None,
        timeout: int = 300,
        env_names: tuple[str, ...] | list[str] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Run a completion validator without shell interpolation.

        Environment values are inherited by Docker from the current process;
        only validated names enter argv, so secrets are absent from receipts and
        command logs.
        """
        command_argv = tuple(argv)
        if not command_argv or any(not isinstance(arg, str) for arg in command_argv):
            raise ValueError("validation argv must contain at least one string")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValueError("validation timeout must be a positive integer")
        effective_cwd = cwd or self.container_workdir
        self._validate_absolute_container_path(effective_cwd, "validation cwd")
        environment_names = tuple(env_names)
        if any(not _ENV_IDENTIFIER.fullmatch(name) for name in environment_names):
            raise ValueError("validation environment names must be safe identifiers")
        command = [self.docker_binary, "exec", "--workdir", effective_cwd]
        for name in environment_names:
            command.extend(["--env", name])
        command.extend([self.container, *command_argv])
        return await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
