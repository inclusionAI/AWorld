import asyncio
import base64
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import platform
import re
import shlex
import signal
import subprocess
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Union
import os

from dotenv import load_dotenv
from pydantic.fields import FieldInfo
from mcp.server.fastmcp import Context
from mcp.server import FastMCP
from mcp.types import TextContent
from pydantic import Field, BaseModel

try:
    from .background_keywords import LONG_RUNNING_KEYWORDS
except ImportError:  # Direct script execution used by the stdio config.
    from background_keywords import LONG_RUNNING_KEYWORDS

_disable_auto_dotenv = os.environ.get("AWORLD_DISABLE_AUTO_DOTENV", "").strip().lower()
if _disable_auto_dotenv not in {"1", "true", "yes", "on"}:
    load_dotenv()
workspace = Path.cwd()

# Allow customizing the leading icon in the terminal card output
TERMINAL_ICON = os.getenv("TERMINAL_ICON", "🖥️")

command_history: list[dict] = []
max_history_size = 50
_MAX_INLINE_STREAM_CHARS = 16_384
_DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
_MAX_COMMAND_TIMEOUT_SECONDS = 3_600
_DEFAULT_TOTAL_CAPTURE_BYTES = 1 * 1024 * 1024
_HARD_MAX_TOTAL_CAPTURE_BYTES = 16 * 1024 * 1024
_MIN_TOTAL_CAPTURE_BYTES = 2 * 1024
_STREAM_READ_CHUNK_BYTES = 64 * 1024
_BACKGROUND_CAPTURE_FLUSH_SECONDS = 0.1
_CAPTURE_LIMIT_ENV = "AWORLD_TERMINAL_CAPTURE_MAX_BYTES"
_CAPTURE_LIMIT_ENV_ALIAS = "TERMINAL_CAPTURE_MAX_BYTES"
_DEFAULT_ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
_HARD_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_MIN_ARTIFACT_MAX_BYTES = 2 * 1024
_DEFAULT_ARTIFACT_READ_MAX_BYTES = 1 * 1024 * 1024
_HARD_MAX_ARTIFACT_READ_BYTES = 16 * 1024 * 1024
_ARTIFACT_LIMIT_ENV = "AWORLD_TERMINAL_ARTIFACT_MAX_BYTES"
_ARTIFACT_READ_LIMIT_ENV = "AWORLD_TERMINAL_ARTIFACT_READ_MAX_BYTES"
_ARTIFACT_DIRECTORY_ENV = "AWORLD_TERMINAL_ARTIFACT_DIR"
_TASK_DEADLINE_ENV = "AWORLD_TASK_DEADLINE_EPOCH_SECONDS"
_COMPLETION_RESERVE_ENV = "AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS"
_MAX_TIMEOUT_ENV = "AWORLD_TERMINAL_MAX_TIMEOUT_SECONDS"
_DEFAULT_COMPLETION_RESERVE_SECONDS = 15.0
_ARTIFACT_REF_PREFIX = "aworld-terminal-output://sha256/"
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MAX_ENV_OVERRIDES = 128
_MAX_ENV_OVERRIDE_BYTES = 64 * 1024

# Keep strong references to drain-only tasks for background children that retain
# inherited stdout/stderr descriptors after their launching shell has exited.
# Each task drops all captured bytes before being registered here.
_background_drain_tasks: set[asyncio.Task[None]] = set()

# Get current platform info
platform_info = {
    "system": platform.system(),
    "platform": platform.platform(),
    "architecture": platform.architecture()[0],
}


class ActionResponse(BaseModel):
    r"""Protocol: MCP Action Response"""

    success: bool = Field(
        default=False, description="Whether the action is successfully executed"
    )
    message: Any = Field(default=None, description="The execution result of the action")
    metadata: dict[str, Any] = Field(
        default={}, description="The metadata of the action"
    )


class CommandResult(BaseModel):
    """Individual command execution result with structured data."""

    command: str
    success: bool
    stdout: str
    stderr: str
    return_code: int
    duration: str
    timestamp: str
    output_truncated: bool = False
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    stdout_omitted_bytes: int = 0
    stderr_omitted_bytes: int = 0
    capture_limit_bytes: int = _DEFAULT_TOTAL_CAPTURE_BYTES
    capture_complete: bool = True
    background_output_detached: bool = False
    timed_out: bool = False
    stdout_output_policy: dict[str, Any] = Field(default_factory=dict)
    stderr_output_policy: dict[str, Any] = Field(default_factory=dict)


class TerminalMetadata(BaseModel):
    """Metadata for terminal operation results."""

    command: str
    platform: str
    working_directory: str
    timeout_seconds: float
    requested_timeout_seconds: float | None = None
    remaining_task_seconds: float | None = None
    timeout_limited_by: str | None = None
    execution_time: float | None = None
    return_code: int | None = None
    safety_check_passed: bool = True
    error_type: str | None = None
    history_count: int | None = None
    output_data: str | None = None
    output_truncated: bool = False
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    stdout_omitted_bytes: int = 0
    stderr_omitted_bytes: int = 0
    capture_limit_bytes: int = _DEFAULT_TOTAL_CAPTURE_BYTES
    capture_complete: bool = True
    background_output_detached: bool = False
    capture_strategy: str = "bounded_head_tail_drain"
    environment_keys: list[str] = Field(default_factory=list)
    output_policy: dict[str, dict[str, Any]] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandTimeoutDecision:
    requested_seconds: float
    effective_seconds: float
    remaining_task_seconds: float | None
    limited_by: str | None


def _get_total_capture_limit_bytes() -> int:
    """Return the configured combined stdout/stderr retention budget.

    The value is intentionally clamped to a framework-owned hard maximum.  This
    prevents a task-controlled environment variable from restoring unbounded
    capture.  The budget is split between stdout and stderr so their combined
    retained payload can never exceed the configured value.
    """

    raw_value = os.environ.get(_CAPTURE_LIMIT_ENV)
    if raw_value is None:
        raw_value = os.environ.get(_CAPTURE_LIMIT_ENV_ALIAS)
    try:
        configured = (
            int(raw_value) if raw_value is not None else _DEFAULT_TOTAL_CAPTURE_BYTES
        )
    except (TypeError, ValueError):
        configured = _DEFAULT_TOTAL_CAPTURE_BYTES
    return max(
        _MIN_TOTAL_CAPTURE_BYTES,
        min(configured, _HARD_MAX_TOTAL_CAPTURE_BYTES),
    )


def _bounded_env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = os.environ.get(name)
    try:
        configured = int(raw_value) if raw_value is not None else default
    except (TypeError, ValueError):
        configured = default
    return max(minimum, min(configured, maximum))


def _get_artifact_max_bytes() -> int:
    return _bounded_env_int(
        _ARTIFACT_LIMIT_ENV,
        _DEFAULT_ARTIFACT_MAX_BYTES,
        minimum=_MIN_ARTIFACT_MAX_BYTES,
        maximum=_HARD_MAX_ARTIFACT_BYTES,
    )


def _get_artifact_read_max_bytes() -> int:
    return _bounded_env_int(
        _ARTIFACT_READ_LIMIT_ENV,
        _DEFAULT_ARTIFACT_READ_MAX_BYTES,
        minimum=1,
        maximum=_HARD_MAX_ARTIFACT_READ_BYTES,
    )


def _artifact_directory() -> Path:
    configured = os.environ.get(_ARTIFACT_DIRECTORY_ENV, "").strip()
    root = (
        Path(configured).expanduser()
        if configured
        else workspace / ".aworld" / "artifacts" / "terminal-output"
    ).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _positive_env_float(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value < 0:
        return default
    return value


def _resolve_command_timeout(
    requested: float,
    *,
    now_epoch: float | None = None,
) -> CommandTimeoutDecision:
    """Clamp a Tool timeout to framework policy and an optional task deadline."""

    if isinstance(requested, bool):
        raise ValueError("timeout must be a positive finite number")
    requested_seconds = float(requested)
    if not math.isfinite(requested_seconds) or requested_seconds <= 0:
        raise ValueError("timeout must be a positive finite number")

    env_timeout = os.environ.get("TERMINAL_TIMEOUT")
    if env_timeout is not None:
        try:
            configured_timeout = float(env_timeout)
        except (TypeError, ValueError):
            configured_timeout = requested_seconds
        if math.isfinite(configured_timeout) and configured_timeout > 0:
            requested_seconds = configured_timeout

    configured_max = _positive_env_float(
        _MAX_TIMEOUT_ENV,
        float(_MAX_COMMAND_TIMEOUT_SECONDS),
    )
    configured_max = max(1.0, min(configured_max, float(_MAX_COMMAND_TIMEOUT_SECONDS)))
    effective = min(requested_seconds, configured_max)
    limited_by = "terminal_maximum" if effective < requested_seconds else None

    raw_deadline = os.environ.get(_TASK_DEADLINE_ENV)
    if raw_deadline is None:
        return CommandTimeoutDecision(
            requested_seconds=requested_seconds,
            effective_seconds=effective,
            remaining_task_seconds=None,
            limited_by=limited_by,
        )
    try:
        deadline = float(raw_deadline)
    except (TypeError, ValueError):
        deadline = math.nan
    if not math.isfinite(deadline):
        return CommandTimeoutDecision(
            requested_seconds=requested_seconds,
            effective_seconds=effective,
            remaining_task_seconds=None,
            limited_by=limited_by,
        )

    now = time.time() if now_epoch is None else float(now_epoch)
    remaining = max(0.0, deadline - now)
    reserve = _positive_env_float(
        _COMPLETION_RESERVE_ENV,
        _DEFAULT_COMPLETION_RESERVE_SECONDS,
    )
    available = max(0.0, remaining - reserve)
    if available <= 0:
        return CommandTimeoutDecision(
            requested_seconds=requested_seconds,
            effective_seconds=0.0,
            remaining_task_seconds=remaining,
            limited_by="task_deadline_exhausted",
        )
    if available < effective:
        effective = available
        limited_by = "task_deadline"
    return CommandTimeoutDecision(
        requested_seconds=requested_seconds,
        effective_seconds=effective,
        remaining_task_seconds=remaining,
        limited_by=limited_by,
    )


def _resolve_working_directory(cwd: str | None) -> Path:
    if cwd is None or not str(cwd).strip():
        return workspace
    candidate = Path(str(cwd)).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    if not resolved.exists():
        raise ValueError(f"working directory does not exist: {cwd}")
    if not resolved.is_dir():
        raise ValueError(f"working directory is not a directory: {cwd}")
    return resolved


def _resolve_environment(overrides: Mapping[str, str] | None) -> dict[str, str]:
    if overrides is None:
        return dict(os.environ)
    if not isinstance(overrides, Mapping):
        raise TypeError("env must be an object mapping names to string values")
    if len(overrides) > _MAX_ENV_OVERRIDES:
        raise ValueError(f"env must contain at most {_MAX_ENV_OVERRIDES} entries")
    normalized: dict[str, str] = {}
    total_bytes = 0
    for raw_name, raw_value in overrides.items():
        if not isinstance(raw_name, str) or not _ENV_NAME.fullmatch(raw_name):
            raise ValueError(f"invalid environment variable name: {raw_name!r}")
        if not isinstance(raw_value, str) or "\x00" in raw_value:
            raise ValueError(f"environment variable {raw_name!r} must be a NUL-free string")
        total_bytes += len(raw_name.encode()) + len(raw_value.encode())
        if total_bytes > _MAX_ENV_OVERRIDE_BYTES:
            raise ValueError(
                f"env exceeds the {_MAX_ENV_OVERRIDE_BYTES}-byte override limit"
            )
        normalized[raw_name] = raw_value
    return {**os.environ, **normalized}


class _ArtifactWriter:
    """Finite streaming sink for one stdout/stderr artifact."""

    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max_bytes
        descriptor, temporary_name = tempfile.mkstemp(prefix=".capture-", dir=root)
        os.chmod(temporary_name, 0o600)
        self._stream = os.fdopen(descriptor, "wb")
        self._temporary_path = Path(temporary_name)
        self._digest = hashlib.sha256()
        self.written_bytes = 0
        self._closed = False

    def feed(self, chunk: bytes) -> None:
        if self._closed or not chunk or self.written_bytes >= self.max_bytes:
            return
        retained = chunk[: self.max_bytes - self.written_bytes]
        if retained:
            self._stream.write(retained)
            self._digest.update(retained)
            self.written_bytes += len(retained)

    def finalize(
        self,
        *,
        persist: bool,
        stream_total_bytes: int,
        capture_complete: bool,
    ) -> dict[str, Any]:
        if self._closed:
            return {}
        self._closed = True
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()
        if not persist or self.written_bytes == 0:
            self._temporary_path.unlink(missing_ok=True)
            return {}
        digest = self._digest.hexdigest()
        final_path = self.root / f"{digest}.bin"
        if final_path.exists():
            self._temporary_path.unlink(missing_ok=True)
        else:
            os.replace(self._temporary_path, final_path)
            os.chmod(final_path, 0o600)
        return {
            "artifact_ref": f"{_ARTIFACT_REF_PREFIX}{digest}",
            "content_sha256": digest,
            "raw_bytes": self.written_bytes,
            "stream_total_bytes": stream_total_bytes,
            "artifact_complete": (
                capture_complete and self.written_bytes == stream_total_bytes
            ),
        }

    def discard(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stream.close()
        self._temporary_path.unlink(missing_ok=True)


class _BoundedStreamCapture:
    """Incrementally retain a byte-bounded head and tail of one pipe.

    Once the limit is crossed, all later bytes are still read from the pipe to
    avoid subprocess backpressure, but only the fixed-size head/tail window is
    retained.  No command output is spooled to disk.
    """

    def __init__(
        self,
        stream_name: str,
        max_bytes: int,
        *,
        artifact_writer: _ArtifactWriter | None = None,
    ) -> None:
        self.stream_name = stream_name
        self.max_bytes = max(1, int(max_bytes))
        self._artifact_writer = artifact_writer
        self.total_bytes = 0
        self._head_limit = self.max_bytes // 2
        self._tail_limit = self.max_bytes - self._head_limit
        self._head = bytearray()
        self._tail_chunks: deque[bytes] = deque()
        self._tail_bytes = 0
        self._discard_only = False

    @property
    def truncated(self) -> bool:
        return self.total_bytes > self.max_bytes

    @property
    def retained_bytes(self) -> int:
        if self._discard_only:
            return 0
        return len(self._head) + self._tail_bytes

    @property
    def omitted_bytes(self) -> int:
        return max(0, self.total_bytes - self.retained_bytes)

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.total_bytes += len(chunk)
        if self._artifact_writer is not None:
            self._artifact_writer.feed(chunk)
        if self._discard_only:
            return

        remaining_head = self._head_limit - len(self._head)
        if remaining_head > 0:
            head_part = chunk[:remaining_head]
            self._head.extend(head_part)
            chunk = chunk[len(head_part) :]
        if chunk:
            self._append_tail(chunk, self._tail_limit)

    def _append_tail(self, chunk: bytes, tail_limit: int) -> None:
        if len(chunk) >= tail_limit:
            self._tail_chunks.clear()
            self._tail_chunks.append(bytes(chunk[-tail_limit:]))
            self._tail_bytes = tail_limit
            return

        self._tail_chunks.append(bytes(chunk))
        self._tail_bytes += len(chunk)
        overflow = self._tail_bytes - tail_limit
        while overflow > 0 and self._tail_chunks:
            first = self._tail_chunks[0]
            if len(first) <= overflow:
                self._tail_chunks.popleft()
                self._tail_bytes -= len(first)
                overflow -= len(first)
            else:
                self._tail_chunks[0] = first[overflow:]
                self._tail_bytes -= overflow
                overflow = 0

    def render(self) -> str:
        if self._discard_only:
            return ""
        head = bytes(self._head).decode("utf-8", errors="replace")
        tail = b"".join(self._tail_chunks).decode("utf-8", errors="replace")
        if not self.truncated:
            return head + tail
        return (
            f"{head}\n\n"
            f"[terminal {self.stream_name} truncated: {self.omitted_bytes} bytes omitted; "
            "captured head and tail; complete stream was drained without retention]"
            f"\n\n{tail}"
        )

    def discard_retained_bytes(self) -> None:
        """Switch a detached background stream to constant-memory drain-only mode."""

        self._discard_only = True
        self._head.clear()
        self._tail_chunks.clear()
        self._tail_bytes = 0

    def finalize_artifact(self, *, capture_complete: bool) -> dict[str, Any]:
        if self._artifact_writer is None:
            return {}
        return self._artifact_writer.finalize(
            persist=self.truncated,
            stream_total_bytes=self.total_bytes,
            capture_complete=capture_complete,
        )

    def discard_artifact(self) -> None:
        if self._artifact_writer is not None:
            self._artifact_writer.discard()


def _bounded_inline_stream(
    value: str,
    *,
    max_chars: int = _MAX_INLINE_STREAM_CHARS,
) -> str:
    """Keep command output bounded before it enters model context."""

    if len(value) <= max_chars:
        return value
    head_chars = max(max_chars // 2, 1)
    tail_chars = max(max_chars - head_chars, 1)
    omitted_chars = max(0, len(value) - head_chars - tail_chars)
    return (
        f"{value[:head_chars]}\n\n"
        f"[terminal output truncated: {omitted_chars} chars omitted; "
        "redirect the complete output to a file and inspect a bounded excerpt]"
        f"\n\n{value[-tail_chars:]}"
    )


# Read log level from environment variable, default to WARNING for clean CLI output
_log_level = (
    os.environ.get("MCP_LOG_LEVEL")
    or os.environ.get("LOG_LEVEL")
    or os.environ.get("LOGLEVEL")
    or "WARNING"
)

mcp = FastMCP(
    "terminal-server",
    log_level=_log_level,
    port=8081,
    instructions="""
Terminal MCP Server

This module provides MCP server functionality for executing terminal commands safely.
It supports command execution with timeout controls and returns LLM-friendly formatted results.

Key features:
- Execute terminal commands with configurable timeouts
- Cross-platform command execution support
- Bounded output and command history tracking
- Safety checks for dangerous commands
- LLM-optimized output formatting

Main tool:
- run_code: Execute a terminal command with safety checks
- read_output_artifact: Retrieve a bounded range from truncated command output
""",
)


async def send_command_card(
    ctx: Context, command_id: str, command: str, output: str, workspace: Path
):
    try:
        command_tool_card = {
            "type": "tool_call_card_command_execute",
            "custom_output": f"{TERMINAL_ICON} Terminal $ {command}",
            "card_data": {
                "title": "Termainl Command Execute",
                "command_id": command_id,
                "command": command,
                "result": {"message": output},
                "metadata": {
                    "working_directory": str(workspace),
                },
            },
        }
        message = f"""\
\n\n
```tool_card
{json.dumps(command_tool_card, indent=2, ensure_ascii=False)}
```
\n\n
"""
        if ctx:
            await ctx.report_progress(progress=0.0, total=1.0, message=message)
    except Exception:
        logging.error(f"Error sending command card: {traceback.format_exc()}")


@mcp.tool(
    description="""
Execute a terminal command with safety checks and timeout controls.

        This tool provides secure command execution with:
        - Cross-platform compatibility (Windows, macOS, Linux)
        - Configurable timeout controls
        - Safety checks for dangerous commands
        - LLM-optimized result formatting
        - Command history tracking

        Specialized Feature:
        - Execute Python code and output the result to stdout
            - Example (Directly execute simple Python code): `python -c "nums = [1, 2, 3, 4]\nsum_of_nums = sum(nums)\nprint(f'{sum_of_nums=}')"`
            - Example (Execute code from a file): `python my_script.py`
"""
)
async def run_code(
    ctx: Context,
    code: str = Field(description="Terminal command to execute"),
    timeout: float = Field(
        default=_DEFAULT_COMMAND_TIMEOUT_SECONDS,
        description="Command timeout in seconds (default: 300, max: 3600)",
    ),
    output_format: str = Field(
        default="structured",
        description="Output format: 'structured', 'markdown', 'json', or 'text'",
    ),
    cwd: Optional[str] = Field(
        default=None,
        description="Optional working directory; relative paths resolve from the workspace",
    ),
    env: Optional[dict[str, str]] = Field(
        default=None,
        description="Optional per-command environment overrides",
    ),
) -> Union[str, TextContent]:
    # Normalize parameters: when using MCP tool schemas, the raw values may be
    # FieldInfo instances. In that case, fall back to their default values.
    if isinstance(code, FieldInfo):
        command = code.default
    else:
        command = code

    if isinstance(timeout, FieldInfo):
        timeout = timeout.default

    if isinstance(output_format, FieldInfo):
        output_format = output_format.default

    if isinstance(cwd, FieldInfo):
        cwd = cwd.default
    if isinstance(env, FieldInfo):
        env = env.default

    try:
        timeout_decision = _resolve_command_timeout(timeout)
        working_directory = _resolve_working_directory(cwd)
        command_environment = _resolve_environment(env)
        environment_keys = sorted(env or {})
        if timeout_decision.effective_seconds <= 0:
            action_response = ActionResponse(
                success=False,
                message={
                    "stdout": "",
                    "stderr": "Task execution deadline is reserved for completion",
                },
                metadata=TerminalMetadata(
                    command=str(command),
                    platform=platform_info["system"],
                    working_directory=str(working_directory),
                    timeout_seconds=0,
                    requested_timeout_seconds=timeout_decision.requested_seconds,
                    remaining_task_seconds=timeout_decision.remaining_task_seconds,
                    timeout_limited_by=timeout_decision.limited_by,
                    safety_check_passed=True,
                    error_type="task_budget_exhausted",
                    environment_keys=environment_keys,
                ).model_dump(),
            )
            return TextContent(
                type="text",
                text=json.dumps(action_response.model_dump()),
                **{"metadata": {}},
            )
        # Safety check
        is_safe, safety_reason = _check_command_safety(command)
        if not is_safe:
            action_response = ActionResponse(
                success=False,
                message=f"Command rejected for security reasons: {safety_reason}",
                metadata=TerminalMetadata(
                    command=command,
                    platform=platform_info["system"],
                    working_directory=str(working_directory),
                    timeout_seconds=timeout_decision.effective_seconds,
                    requested_timeout_seconds=timeout_decision.requested_seconds,
                    remaining_task_seconds=timeout_decision.remaining_task_seconds,
                    timeout_limited_by=timeout_decision.limited_by,
                    safety_check_passed=False,
                    error_type="security_violation",
                    environment_keys=environment_keys,
                ).model_dump(),
            )
            # await send_command_card(
            #     ctx,
            #     command_id,
            #     command=command,
            #     output=safety_reason,
            #     workspace=workspace,
            # )
            return TextContent(
                type="text",
                text=json.dumps(
                    action_response.model_dump()
                ),  # Empty string instead of None
                **{"metadata": {}},  # Pass as additional fields
            )

        logging.info(f"🔧 Executing command: {command}")

        # Execute command
        start_time = time.time()
        result = await _execute_command_async(
            command,
            timeout_decision.effective_seconds,
            cwd=working_directory,
            env=command_environment,
        )
        execution_time = time.time() - start_time

        # Format output
        formatted_output = _format_command_output(result, output_format)

        # Create metadata
        metadata = TerminalMetadata(
            command=command,
            platform=platform_info["system"],
            working_directory=str(working_directory),
            timeout_seconds=timeout_decision.effective_seconds,
            requested_timeout_seconds=timeout_decision.requested_seconds,
            remaining_task_seconds=timeout_decision.remaining_task_seconds,
            timeout_limited_by=timeout_decision.limited_by,
            execution_time=execution_time,
            return_code=result.return_code,
            safety_check_passed=True,
            output_truncated=result.output_truncated,
            stdout_total_bytes=result.stdout_total_bytes,
            stderr_total_bytes=result.stderr_total_bytes,
            stdout_omitted_bytes=result.stdout_omitted_bytes,
            stderr_omitted_bytes=result.stderr_omitted_bytes,
            capture_limit_bytes=result.capture_limit_bytes,
            capture_complete=result.capture_complete,
            background_output_detached=result.background_output_detached,
            environment_keys=environment_keys,
            output_policy={
                "stdout": result.stdout_output_policy,
                "stderr": result.stderr_output_policy,
            },
        )

        if result.success:
            logging.info(
                "✅ Command completed successfully",
            )
        else:
            logging.info(f"❌ Command failed with return code {result.return_code}")
            metadata.error_type = "timeout" if result.timed_out else "execution_failure"

        action_response = ActionResponse(
            success=result.success,
            message=formatted_output,
            metadata=metadata.model_dump(),
        )
        # await send_command_card(
        #     ctx,
        #     command_id,
        #     command=command,
        #     output=metadata.output_data,
        #     workspace=workspace,
        # )
        return TextContent(
            type="text",
            text=json.dumps(
                action_response.model_dump()
            ),  # Empty string instead of None
            # Terminal output is transient execution evidence, not a workspace
            # artifact.  Repeating the response as artifact_data doubled the
            # serialized payload and could persist sensitive excerpts.
            **{"metadata": {}},
        )

    except Exception as e:
        error_msg = f"Failed to execute command: {str(e)}"
        logging.error(f"Command execution error: {traceback.format_exc()}")

        action_response = ActionResponse(
            success=False,
            message=error_msg,
            metadata=TerminalMetadata(
                command=command,
                platform=platform_info["system"],
                working_directory=str(cwd or workspace),
                timeout_seconds=0,
                safety_check_passed=True,
                error_type="internal_error",
            ).model_dump(),
        )
        return TextContent(
            type="text",
            text=json.dumps(
                action_response.model_dump()
            ),  # Empty string instead of None
            **{"metadata": {}},  # Pass as additional fields
        )


def _shell_command_segments(command: str) -> list[list[str]]:
    """Return shell command words without treating quoted examples as actions."""

    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = "#"
    segments: list[list[str]] = []
    current: list[str] = []
    for token in lexer:
        if token and all(character in ";&|()\n" for character in token):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _command_words(segment: list[str]) -> tuple[str, list[str]]:
    """Strip common execution wrappers and return executable plus arguments."""

    words = list(segment)
    while words:
        executable = Path(words[0]).name.lower()
        if executable in {"command", "builtin"}:
            words.pop(0)
            continue
        if executable == "sudo":
            words.pop(0)
            while words and words[0].startswith("-"):
                option = words.pop(0)
                if option in {"-u", "-g", "-h", "-p", "-C", "-T", "-R", "-D"} and words:
                    words.pop(0)
            continue
        if executable == "env":
            words.pop(0)
            while words and (words[0].startswith("-") or "=" in words[0]):
                words.pop(0)
            continue
        return executable, words[1:]
    return "", []


def _is_broad_rm_target(target: str) -> bool:
    raw = target.strip()
    if not raw:
        return False
    expanded = os.path.expanduser(os.path.expandvars(raw))
    normalized = os.path.normpath(expanded)
    if normalized == "/" or raw in {"/*", "/.*", "/{*,.*}"}:
        return True
    home = str(Path.home().resolve())
    if normalized == home or raw in {"~", "$HOME", "${HOME}"}:
        return True
    return raw in {"~/*", "$HOME/*", "${HOME}/*", "~/.*", "$HOME/.*", "${HOME}/.*"}


def _rm_recurses(args: list[str]) -> bool:
    for value in args:
        if value == "--":
            break
        if value.startswith("--"):
            if value == "--recursive":
                return True
            continue
        if value.startswith("-") and any(flag in value[1:] for flag in ("r", "R")):
            return True
    return False


def _rm_targets(args: list[str]) -> list[str]:
    targets: list[str] = []
    options_done = False
    for value in args:
        if not options_done and value == "--":
            options_done = True
            continue
        if not options_done and value.startswith("-"):
            continue
        targets.append(value)
    return targets


def _dangerous_device_output(args: list[str]) -> str | None:
    for value in args:
        if not value.lower().startswith("of="):
            continue
        target = value[3:]
        if re.match(
            r"^/dev/(?:sd|hd|vd|xvd|nvme|mmcblk|disk|rdisk|mapper/)",
            target,
            re.IGNORECASE,
        ):
            return target
    return None


def _check_command_safety(command: str) -> tuple[bool, str | None]:
    """Reject broad host-destructive operations without blocking scoped cleanup."""

    if not isinstance(command, str) or not command.strip():
        return False, "Command must be a non-empty string"
    compact = "".join(command.split())
    if ":(){:|:&};:" in compact:
        return False, "Command contains a shell fork bomb"
    try:
        segments = _shell_command_segments(command)
    except ValueError as exc:
        return False, f"Command could not be parsed safely: {exc}"
    for segment in segments:
        executable, args = _command_words(segment)
        if not executable:
            continue
        if executable == "rm" and _rm_recurses(args):
            target = next((value for value in _rm_targets(args) if _is_broad_rm_target(value)), None)
            if target is not None:
                return False, f"Recursive removal of broad target is not allowed: {target}"
        if executable == "mkfs" or executable.startswith("mkfs."):
            return False, f"Filesystem formatting command is not allowed: {executable}"
        if executable == "diskpart":
            return False, "Disk partitioning command is not allowed"
        if executable == "dd":
            target = _dangerous_device_output(args)
            if target is not None:
                return False, f"Writing directly to a block device is not allowed: {target}"
        if executable in {"del", "erase"} and any(
            value.lower() in {"c:\\", "c:\\*", "c:/*"} for value in args
        ):
            return False, "Recursive removal of a Windows drive root is not allowed"
    return True, None


def _has_background_operator(command: str) -> bool:
    """Recognize shell ``&`` operators with or without surrounding spaces.

    Quoted/escaped ampersands remain word content. Redirections (``2>&1``,
    ``<&``, ``&>``) and ``&&`` are kept distinct from a bare operator.
    """

    single_quoted = False
    double_quoted = False
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if character == "\\" and not single_quoted:
            escaped = True
            continue
        if character == "'" and not double_quoted:
            single_quoted = not single_quoted
            continue
        if character == '"' and not single_quoted:
            double_quoted = not double_quoted
            continue
        if single_quoted or double_quoted:
            continue
        if character == "#" and (
            index == 0 or command[index - 1].isspace()
        ):
            break
        if character != "&":
            continue
        previous = command[index - 1] if index else ""
        following = command[index + 1] if index + 1 < len(command) else ""
        if (previous and previous in "&<>") or (following and following in "&>"):
            continue
        return True
    return False


def _is_background_process(command: str) -> bool:
    """Determine whether a command should be treated as a background process.

    Detection rules:
    1. Contains a background '&' operator (excluding 2>&1, &&, &>):
       - At the end: `cmd &`
       - In the middle: `cmd1 & cmd2`, `nohup npm run dev ... & echo hello`
    2. Contains any keyword from LONG_RUNNING_KEYWORDS
       (e.g. nohup, npm run dev, docker compose up).

    Args:
        command: The command string to inspect.

    Returns:
        True if the command is likely to spawn a background or long-running
        process; otherwise False.
    """
    cmd_stripped = command.rstrip()
    if _has_background_operator(cmd_stripped):
        return True
    cmd_lower = cmd_stripped.lower()
    for keyword in LONG_RUNNING_KEYWORDS:
        if keyword.lower() in cmd_lower:
            return True
    return False


def _format_command_output(
    result: CommandResult, output_format: str = "structured"
) -> Any:
    """Format command execution results for LLM consumption.

    Args:
        result: Command execution result
        output_format: Format type ('structured', 'markdown', 'json', 'text')

    Returns:
        Formatted string suitable for LLM consumption
    """
    stdout = _bounded_inline_stream(result.stdout)
    stderr = _bounded_inline_stream(result.stderr)
    if output_format == "structured":
        return {"stdout": stdout, "stderr": stderr}
    if output_format == "json":
        return json.dumps(
            result.model_copy(update={"stdout": stdout, "stderr": stderr}).model_dump(),
            indent=2,
        )

    elif output_format == "text":
        output_parts = [
            f"Command: {result.command}",
            f"Status: {'SUCCESS' if result.success else 'FAILED'}",
            f"Duration: {result.duration}",
            f"Return Code: {result.return_code}",
        ]

        if result.output_truncated:
            output_parts.append(
                "Output Capture: TRUNCATED "
                f"(stdout omitted={result.stdout_omitted_bytes} bytes, "
                f"stderr omitted={result.stderr_omitted_bytes} bytes)"
            )
        if not result.capture_complete:
            output_parts.append(
                "Output Capture: INCOMPLETE (background output continues and is drain-only)"
            )

        if stdout:
            output_parts.extend(["\nOutput:", stdout])

        if stderr:
            output_parts.extend(["\nErrors/Warnings:", stderr])

        return "\n".join(output_parts)

    elif output_format == "markdown":
        status_emoji = "✅" if result.success else "❌"

        output_parts = [
            f"# Terminal Command Execution {status_emoji}",
            f"**Command:** `{result.command}`",
            f"**Status:** {'SUCCESS' if result.success else 'FAILED'}",
            f"**Duration:** {result.duration}",
            f"**Return Code:** {result.return_code}",
            f"**Timestamp:** {result.timestamp}",
        ]

        if result.output_truncated:
            output_parts.append(
                "**Output Capture:** TRUNCATED "
                f"(stdout omitted={result.stdout_omitted_bytes} bytes, "
                f"stderr omitted={result.stderr_omitted_bytes} bytes)"
            )
        if not result.capture_complete:
            output_parts.append(
                "**Output Capture:** INCOMPLETE (background output continues and is drain-only)"
            )

        if stdout:
            output_parts.extend(["\n## Output", "```", stdout.strip(), "```"])

        if stderr:
            output_parts.extend(["\n## Errors/Warnings", "```", stderr.strip(), "```"])

        return "\n".join(output_parts)
    raise ValueError(
        "output_format must be one of: structured, markdown, json, text"
    )


async def _drain_stream(
    reader: asyncio.StreamReader,
    capture: _BoundedStreamCapture,
) -> None:
    """Continuously drain one subprocess pipe into a bounded accumulator."""

    while True:
        chunk = await reader.read(_STREAM_READ_CHUNK_BYTES)
        if not chunk:
            return
        capture.feed(chunk)


async def _wait_for_process_returncode(
    process: asyncio.subprocess.Process,
    timeout: float,
) -> None:
    """Wait for the shell itself, independently of inherited pipe closure.

    ``asyncio.subprocess.Process.wait()`` may not resolve until every pipe is
    closed.  A deliberately detached child can inherit those descriptors after
    its launching shell exits, so polling the transport-updated return code is
    required to preserve background-command behavior.
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while process.returncode is None:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        await asyncio.sleep(min(0.01, remaining))


def _consume_background_drain_result(task: asyncio.Task[None]) -> None:
    _background_drain_tasks.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logging.warning("Detached terminal output drain failed", exc_info=True)


def _track_background_drain_tasks(tasks: set[asyncio.Task[None]]) -> None:
    for task in tasks:
        _background_drain_tasks.add(task)
        task.add_done_callback(_consume_background_drain_result)


def _raise_reader_errors(tasks: set[asyncio.Task[None]]) -> None:
    for task in tasks:
        task.result()


def _close_subprocess_pipe_transports(process: asyncio.subprocess.Process) -> None:
    """Best-effort close for pipes whose descendants survived process-group kill."""

    transport = getattr(process, "_transport", None)
    if transport is None:
        return
    for file_descriptor in (1, 2):
        try:
            pipe_transport = transport.get_pipe_transport(file_descriptor)
            if pipe_transport is not None:
                pipe_transport.close()
        except Exception:
            pass


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Terminate a timed-out shell and its descendants, then reap it."""

    if platform_info["system"] == "Windows":
        try:
            taskkill = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.wait_for(taskkill.wait(), timeout=5)
        except (FileNotFoundError, ProcessLookupError, OSError, asyncio.TimeoutError):
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
    elif process.pid:
        # start_new_session=True makes the shell PID the process-group ID.  Kill
        # the group even if the shell has already exited: a background child may
        # still own stdout/stderr and otherwise keep our readers alive forever.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        await asyncio.sleep(0.2)
        try:
            os.killpg(process.pid, 0)
        except (ProcessLookupError, OSError):
            pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except (asyncio.TimeoutError, ProcessLookupError):
        pass


async def _finish_reader_tasks_after_termination(
    process: asyncio.subprocess.Process,
    reader_tasks: set[asyncio.Task[None]],
) -> bool:
    if not reader_tasks:
        return True
    done, pending = await asyncio.wait(reader_tasks, timeout=5)
    _raise_reader_errors(done)
    if not pending:
        return True

    _close_subprocess_pipe_transports(process)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return False


def _record_command_history(
    *,
    command: str,
    start_time: datetime,
    success: bool,
    duration: str,
) -> None:
    command_history.append(
        {
            "timestamp": start_time.isoformat(),
            "command": command,
            "success": success,
            "duration": duration,
        }
    )
    if len(command_history) > max_history_size:
        command_history.pop(0)


async def _execute_command_async(
    command: str,
    timeout: float,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Execute a command while retaining only bounded stdout/stderr excerpts.

    Both foreground and background paths use concurrently drained pipes.  The
    combined retained byte budget is configurable through
    ``AWORLD_TERMINAL_CAPTURE_MAX_BYTES`` (or ``TERMINAL_CAPTURE_MAX_BYTES``)
    and always clamped to a framework hard maximum.  Bytes beyond the budget
    are drained without retention, preventing PIPE deadlocks without growing
    memory or temporary files with command output.
    """

    start_time = datetime.now()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    capture_limit = _get_total_capture_limit_bytes()
    artifact_root = _artifact_directory()
    artifact_limit = _get_artifact_max_bytes()
    stdout_writer = _ArtifactWriter(artifact_root, artifact_limit)
    try:
        stderr_writer = _ArtifactWriter(artifact_root, artifact_limit)
    except Exception:
        stdout_writer.discard()
        raise
    stdout_capture = _BoundedStreamCapture(
        "stdout",
        (capture_limit + 1) // 2,
        artifact_writer=stdout_writer,
    )
    stderr_capture = _BoundedStreamCapture(
        "stderr",
        capture_limit // 2,
        artifact_writer=stderr_writer,
    )
    process: asyncio.subprocess.Process | None = None
    stdout_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None

    try:
        is_background = (
            _is_background_process(command) and platform_info["system"] != "Windows"
        )
        process_options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "shell": True,
            "limit": _STREAM_READ_CHUNK_BYTES,
            "cwd": str(cwd or workspace),
            "env": dict(env) if env is not None else None,
        }
        if platform_info["system"] != "Windows":
            process_options.update(
                executable="/bin/bash",
                start_new_session=True,
            )
        process = await asyncio.create_subprocess_shell(command, **process_options)
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("terminal subprocess pipes were not created")

        stdout_task = asyncio.create_task(_drain_stream(process.stdout, stdout_capture))
        stderr_task = asyncio.create_task(_drain_stream(process.stderr, stderr_capture))
        reader_tasks = {stdout_task, stderr_task}

        timed_out = False
        try:
            remaining = max(0.0, deadline - loop.time())
            await _wait_for_process_returncode(process, timeout=remaining)
        except asyncio.TimeoutError:
            timed_out = True

        background_output_detached = False
        capture_complete = True
        if timed_out:
            await _terminate_process(process)
            capture_complete = await _finish_reader_tasks_after_termination(
                process, reader_tasks
            )
        elif is_background:
            # A shell ending while a background child keeps inherited pipe FDs
            # open must still return promptly.  Give pending reads a small flush
            # window, snapshot them, then retain no more bytes while drain tasks
            # keep the child free from PIPE backpressure.
            done, pending = await asyncio.wait(
                reader_tasks,
                timeout=_BACKGROUND_CAPTURE_FLUSH_SECONDS,
            )
            _raise_reader_errors(done)
            capture_complete = not pending
            background_output_detached = bool(pending)
        else:
            remaining = max(0.0, deadline - loop.time())
            done, pending = await asyncio.wait(reader_tasks, timeout=remaining)
            _raise_reader_errors(done)
            if pending:
                timed_out = True
                await _terminate_process(process)
                capture_complete = await _finish_reader_tasks_after_termination(
                    process, pending
                )

        stdout = stdout_capture.render()
        stderr = stderr_capture.render()
        stdout_total_bytes = stdout_capture.total_bytes
        stderr_total_bytes = stderr_capture.total_bytes
        stdout_omitted_bytes = stdout_capture.omitted_bytes
        stderr_omitted_bytes = stderr_capture.omitted_bytes
        output_truncated = stdout_capture.truncated or stderr_capture.truncated

        if background_output_detached:
            pending_tasks = {
                task
                for task in (stdout_task, stderr_task)
                if task is not None and not task.done()
            }
            if stdout_task in pending_tasks:
                stdout_capture.discard_retained_bytes()
            if stderr_task in pending_tasks:
                stderr_capture.discard_retained_bytes()
            stdout_capture.discard_artifact()
            stderr_capture.discard_artifact()
            _track_background_drain_tasks(pending_tasks)

        stdout_output_policy = stdout_capture.finalize_artifact(
            capture_complete=capture_complete
        )
        stderr_output_policy = stderr_capture.finalize_artifact(
            capture_complete=capture_complete
        )
        for policy, capture in (
            (stdout_output_policy, stdout_capture),
            (stderr_output_policy, stderr_capture),
        ):
            policy.setdefault("artifact_ref", None)
            policy.setdefault("content_sha256", None)
            policy.setdefault("raw_bytes", capture.total_bytes)
            policy.setdefault("stream_total_bytes", capture.total_bytes)
            policy.setdefault("artifact_complete", capture_complete)
            policy.update(
                {
                    "inline_bytes": capture.retained_bytes,
                    "offloaded_bytes": capture.omitted_bytes,
                    "output_truncated": capture.truncated,
                }
            )

        if timed_out:
            timeout_message = f"Command timed out after {timeout:g} seconds"
            stderr = (
                f"{stderr.rstrip()}\n{timeout_message}" if stderr else timeout_message
            )
            return_code = -1
        else:
            return_code = process.returncode if process.returncode is not None else -1

        duration = str(datetime.now() - start_time)
        result = CommandResult(
            command=command,
            success=return_code == 0 and not timed_out,
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            duration=duration,
            timestamp=start_time.isoformat(),
            output_truncated=output_truncated,
            stdout_total_bytes=stdout_total_bytes,
            stderr_total_bytes=stderr_total_bytes,
            stdout_omitted_bytes=stdout_omitted_bytes,
            stderr_omitted_bytes=stderr_omitted_bytes,
            capture_limit_bytes=capture_limit,
            capture_complete=capture_complete,
            background_output_detached=background_output_detached,
            timed_out=timed_out,
            stdout_output_policy=stdout_output_policy,
            stderr_output_policy=stderr_output_policy,
        )
        _record_command_history(
            command=command,
            start_time=start_time,
            success=result.success,
            duration=duration,
        )
        return result

    except asyncio.CancelledError:
        if process is not None:
            await _terminate_process(process)
        tasks = {
            task
            for task in (stdout_task, stderr_task)
            if task is not None and not task.done()
        }
        if process is not None:
            await _finish_reader_tasks_after_termination(process, tasks)
        stdout_capture.discard_artifact()
        stderr_capture.discard_artifact()
        raise
    except Exception as e:
        if process is not None:
            await _terminate_process(process)
        tasks = {
            task
            for task in (stdout_task, stderr_task)
            if task is not None and not task.done()
        }
        if process is not None:
            await _finish_reader_tasks_after_termination(process, tasks)
        stdout_capture.discard_artifact()
        stderr_capture.discard_artifact()
        duration = str(datetime.now() - start_time)
        result = CommandResult(
            command=command,
            success=False,
            stdout="",
            stderr=f"Error executing command: {str(e)}",
            return_code=-1,
            duration=duration,
            timestamp=start_time.isoformat(),
            capture_limit_bytes=capture_limit,
            capture_complete=False,
        )
        _record_command_history(
            command=command,
            start_time=start_time,
            success=False,
            duration=duration,
        )
        return result


def _resolve_artifact_ref(artifact_ref: str) -> tuple[Path, str]:
    if not isinstance(artifact_ref, str) or not artifact_ref.startswith(
        _ARTIFACT_REF_PREFIX
    ):
        raise ValueError("artifact_ref is not a terminal output artifact")
    digest = artifact_ref[len(_ARTIFACT_REF_PREFIX) :]
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("artifact_ref has an invalid checksum")
    root = _artifact_directory()
    path = (root / f"{digest}.bin").resolve()
    if path.parent != root or not path.is_file():
        raise ValueError("artifact_ref is unavailable in this terminal sandbox")
    actual_digest_builder = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_STREAM_READ_CHUNK_BYTES):
            actual_digest_builder.update(chunk)
    actual_digest = actual_digest_builder.hexdigest()
    if actual_digest != digest:
        raise ValueError("artifact content does not match artifact_ref checksum")
    return path, digest


@mcp.tool(
    description=(
        "Read a bounded byte range from a checksummed full terminal output "
        "artifact returned by run_code."
    )
)
async def read_output_artifact(
    ctx: Context,
    artifact_ref: str = Field(
        description="Artifact reference returned in output_policy.artifact_ref"
    ),
    offset: int = Field(default=0, description="Zero-based byte offset"),
    limit: Optional[int] = Field(
        default=None,
        description="Bytes to read; capped by terminal artifact read policy",
    ),
    output: str = Field(default="text", description="text or base64"),
) -> TextContent:
    del ctx
    if isinstance(offset, FieldInfo):
        offset = offset.default
    if isinstance(limit, FieldInfo):
        limit = limit.default
    if isinstance(output, FieldInfo):
        output = output.default
    if offset < 0:
        raise ValueError("offset must be non-negative")
    max_read_bytes = _get_artifact_read_max_bytes()
    requested = max_read_bytes if limit is None else limit
    if requested < 1 or requested > max_read_bytes:
        raise ValueError(f"limit must be between 1 and {max_read_bytes}")
    if output not in {"text", "base64"}:
        raise ValueError("output must be 'text' or 'base64'")
    artifact, digest = _resolve_artifact_ref(artifact_ref)
    total_bytes = artifact.stat().st_size
    with artifact.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(requested)
    next_offset = offset + len(data)
    content = (
        data.decode("utf-8", errors="replace")
        if output == "text"
        else base64.b64encode(data).decode("ascii")
    )
    payload = {
        "type": output,
        "content": content,
        "artifact_ref": artifact_ref,
        "offset": offset,
        "next_offset": next_offset,
        "returned_bytes": len(data),
        "total_bytes": total_bytes,
        "complete": next_offset >= total_bytes,
        "content_sha256": digest,
        "chunk_sha256": hashlib.sha256(data).hexdigest(),
    }
    return TextContent(
        type="text",
        text=json.dumps(payload, ensure_ascii=False),
        **{"metadata": {}},
    )


if __name__ == "__main__":
    import sys

    if _disable_auto_dotenv not in {"1", "true", "yes", "on"}:
        load_dotenv(override=True)
    logging.info("Starting terminal-server MCP server!")
    # Default streamable-http (compat with start_tool_servers.sh); use stdio when --stdio or MCP_TRANSPORT=stdio
    use_stdio = (
        "--stdio" in sys.argv
        or os.environ.get("MCP_TRANSPORT", "").strip().lower() == "stdio"
    )
    try:
        if use_stdio:
            mcp.run(transport="stdio")
        else:
            mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logging.info("Terminal MCP server stopped")
