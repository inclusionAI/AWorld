import asyncio
import base64
from collections import deque
from dataclasses import replace
import hashlib
import json
import logging
import math
import platform
import re
import shlex
import shutil
import signal
import stat as stat_module
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Union
import os

import bashlex
from bashlex import ast as shell_ast
from bashlex import flags as shell_flags
from bashlex import parser as shell_parser
from bashlex import subst as shell_subst
from bashlex import tokenizer as shell_tokenizer
from bashlex import utils as shell_utils
from dotenv import load_dotenv
from pydantic.fields import FieldInfo
from mcp.server.fastmcp import Context
from mcp.server import FastMCP
from mcp.types import TextContent
from pydantic import Field, BaseModel

from aworld.sandbox.terminal_receipt import (
    TerminalExecutionPlan,
    build_terminal_execution_receipt,
    plan_terminal_execution,
)
from aworld.sandbox.task_budget import (
    DEFAULT_COMPLETION_RESERVE_SECONDS,
    FrameworkTaskBudget,
    ToolLeaseDecision,
    ToolLeaseStage,
    resolve_tool_lease,
)

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
_TASK_LEASE_FRACTION_ENV = "AWORLD_TERMINAL_TASK_LEASE_FRACTION"
_TASK_LEASE_MIN_ENV = "AWORLD_TERMINAL_TASK_LEASE_MIN_SECONDS"
_DEFAULT_COMPLETION_RESERVE_SECONDS = DEFAULT_COMPLETION_RESERVE_SECONDS
_DEFAULT_TASK_LEASE_FRACTION = 0.25
_DEFAULT_TASK_LEASE_MIN_SECONDS = 15.0
_ARTIFACT_REF_PREFIX = "aworld-terminal-output://sha256/"
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MAX_ENV_OVERRIDES = 128
_MAX_ENV_OVERRIDE_BYTES = 64 * 1024
AWORLD_SHELL_PARSER_VERSION = 1

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
    timeout_policy_seconds: float | None = None
    timeout_policy_override: str | None = None
    remaining_task_seconds: float | None = None
    timeout_limited_by: str | None = None
    task_budget_stage: str | None = None
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
    terminal_execution_receipt: dict[str, Any] | None = None


CommandTimeoutDecision = ToolLeaseDecision


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


def _task_lease_fraction() -> float:
    value = _positive_env_float(
        _TASK_LEASE_FRACTION_ENV,
        _DEFAULT_TASK_LEASE_FRACTION,
    )
    if value <= 0 or value > 1:
        return _DEFAULT_TASK_LEASE_FRACTION
    return value


def _task_lease_fraction_is_explicit() -> bool:
    raw_value = os.environ.get(_TASK_LEASE_FRACTION_ENV)
    if raw_value is None:
        return False
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and 0 < value <= 1


def _resolve_command_timeout(
    requested: float,
    *,
    now_epoch: float | None = None,
    task_deadline_epoch_seconds: float | None = None,
    completion_reserve_seconds: float | None = None,
    task_budget_stage: str = ToolLeaseStage.EXECUTE.value,
    task_remaining_seconds: float | None = None,
    task_budget_captured_at_epoch_seconds: float | None = None,
    task_budget: Mapping[str, Any] | None = None,
) -> CommandTimeoutDecision:
    """Clamp a Tool timeout to framework policy and an optional task deadline."""

    if isinstance(requested, bool):
        raise ValueError("timeout must be a positive finite number")
    requested_seconds = float(requested)
    if not math.isfinite(requested_seconds) or requested_seconds <= 0:
        raise ValueError("timeout must be a positive finite number")

    policy_seconds = requested_seconds
    policy_override = None
    env_timeout = os.environ.get("TERMINAL_TIMEOUT")
    if env_timeout is not None:
        try:
            configured_timeout = float(env_timeout)
        except (TypeError, ValueError):
            configured_timeout = requested_seconds
        if math.isfinite(configured_timeout) and configured_timeout > 0:
            policy_seconds = configured_timeout
            policy_override = "terminal_timeout"

    configured_max = _positive_env_float(
        _MAX_TIMEOUT_ENV,
        float(_MAX_COMMAND_TIMEOUT_SECONDS),
    )
    configured_max = max(1.0, min(configured_max, float(_MAX_COMMAND_TIMEOUT_SECONDS)))
    authoritative_budget = FrameworkTaskBudget.from_hidden_dict(task_budget)
    if task_budget is not None and authoritative_budget is None:
        # A malformed purported framework payload never gains authority and
        # must not be repaired from model/process-controlled values.
        authoritative_budget = FrameworkTaskBudget()
    if authoritative_budget is None:
        raw_deadline: Any = task_deadline_epoch_seconds
        if raw_deadline is None:
            raw_deadline = os.environ.get(_TASK_DEADLINE_ENV)
        try:
            deadline = float(raw_deadline) if raw_deadline is not None else None
        except (TypeError, ValueError):
            deadline = None
        if deadline is not None and math.isfinite(deadline):
            now = time.time() if now_epoch is None else float(now_epoch)
            remaining = task_remaining_seconds
            if (
                isinstance(remaining, bool)
                or not isinstance(remaining, (int, float))
                or not math.isfinite(float(remaining))
                or remaining < 0
            ):
                remaining = max(0.0, deadline - now)
            try:
                stage = ToolLeaseStage(task_budget_stage)
            except (TypeError, ValueError):
                stage = ToolLeaseStage.EXECUTE
            authoritative_budget = FrameworkTaskBudget(
                bounded=True,
                stage=stage,
                deadline_epoch_seconds=deadline,
                remaining_seconds=float(remaining),
                completion_reserve_seconds=(
                    float(completion_reserve_seconds)
                    if isinstance(completion_reserve_seconds, (int, float))
                    and not isinstance(completion_reserve_seconds, bool)
                    and math.isfinite(float(completion_reserve_seconds))
                    and completion_reserve_seconds >= 0
                    else _positive_env_float(
                        _COMPLETION_RESERVE_ENV,
                        _DEFAULT_COMPLETION_RESERVE_SECONDS,
                    )
                ),
                captured_at_epoch_seconds=(
                    float(task_budget_captured_at_epoch_seconds)
                    if isinstance(task_budget_captured_at_epoch_seconds, (int, float))
                    and not isinstance(task_budget_captured_at_epoch_seconds, bool)
                    else now
                ),
            )

    lease_floor = _positive_env_float(
        _TASK_LEASE_MIN_ENV,
        _DEFAULT_TASK_LEASE_MIN_SECONDS,
    )
    decision = resolve_tool_lease(
        requested_seconds,
        budget=authoritative_budget,
        maximum_seconds=configured_max,
        policy_seconds=policy_seconds,
        policy_override=policy_override,
        now_epoch=now_epoch,
        constrained_fraction=_task_lease_fraction(),
        constrained_floor_seconds=lease_floor,
        explicit_fraction_policy=_task_lease_fraction_is_explicit(),
        default_completion_reserve_seconds=_positive_env_float(
            _COMPLETION_RESERVE_ENV,
            _DEFAULT_COMPLETION_RESERVE_SECONDS,
        ),
    )
    if decision.limited_by == "tool_maximum":
        return replace(decision, limited_by="terminal_maximum")
    return decision


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


def _resolve_environment(
    overrides: Mapping[str, str] | None,
    *,
    framework_scope: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    if overrides is not None and not isinstance(overrides, Mapping):
        raise TypeError("env must be an object mapping names to string values")
    if isinstance(overrides, Mapping) and len(overrides) > _MAX_ENV_OVERRIDES:
        raise ValueError(f"env must contain at most {_MAX_ENV_OVERRIDES} entries")
    normalized: dict[str, str] = {}
    total_bytes = 0
    for raw_name, raw_value in (overrides or {}).items():
        if not isinstance(raw_name, str) or not _ENV_NAME.fullmatch(raw_name):
            raise ValueError(f"invalid environment variable name: {raw_name!r}")
        if not isinstance(raw_value, str) or "\x00" in raw_value:
            raise ValueError(
                f"environment variable {raw_name!r} must be a NUL-free string"
            )
        total_bytes += len(raw_name.encode()) + len(raw_value.encode())
        if total_bytes > _MAX_ENV_OVERRIDE_BYTES:
            raise ValueError(
                f"env exceeds the {_MAX_ENV_OVERRIDE_BYTES}-byte override limit"
            )
        normalized[raw_name] = raw_value
    resolved = {**os.environ, **normalized}
    if isinstance(framework_scope, Mapping):
        for source_name, environment_name in (
            ("task_id", "AWORLD_TASK_ID"),
            ("session_id", "AWORLD_SESSION_ID"),
            ("task_epoch", "AWORLD_TASK_EPOCH"),
        ):
            value = framework_scope.get(source_name)
            if value is None or isinstance(value, (dict, list, tuple)):
                continue
            bounded = str(value)[:512]
            if bounded and "\x00" not in bounded:
                resolved[environment_name] = bounded
    return resolved


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
- run_code: Execute Shell by default, or explicit raw Python, with safety checks
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
Execute Shell commands or explicit raw Python with safety checks and timeout controls.

        This tool provides secure command execution with:
        - Cross-platform compatibility (Windows, macOS, Linux)
        - Configurable timeout controls
        - Safety checks for dangerous commands
        - LLM-optimized result formatting
        - Command history tracking

        Language contract:
        - `language="shell"` is the default. It can invoke Python and any other
          executable, for example `python -c "print(1)"` or `python script.py`.
        - Use `language="python"` only when `code` itself is raw Python source.
          Bare Python is never inferred from a Shell request.
"""
)
async def run_code(
    ctx: Context,
    code: str = Field(
        description="Shell command or raw Python source, according to language"
    ),
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
    env_content: Optional[dict[str, Any]] = Field(
        default=None,
        description="Framework-injected task scope; hidden from the model schema",
    ),
    language: Literal["shell", "python"] = Field(
        default="shell",
        description=(
            "Execution language. 'shell' is backward-compatible and may invoke "
            "Python; 'python' executes code as raw Python source."
        ),
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

    if isinstance(language, FieldInfo):
        language = language.default

    if isinstance(output_format, FieldInfo):
        output_format = output_format.default

    if isinstance(cwd, FieldInfo):
        cwd = cwd.default
    if isinstance(env, FieldInfo):
        env = env.default
    if isinstance(env_content, FieldInfo):
        env_content = env_content.default

    if language not in {"shell", "python"}:
        raise ValueError("language must be either 'shell' or 'python'")

    execution_plan = _terminal_execution_plan(command, language=language)
    receipt_plan = execution_plan
    receipt_effect_source = "parser_contract"
    execution_started = False
    execution_result: CommandResult | None = None
    mutation_snapshot: dict[Path, tuple[Any, ...]] | None = None
    read_epochs_before: list[dict[str, Any]] = []

    try:
        task_budget = (
            env_content.get("task_budget")
            if isinstance(env_content, Mapping)
            and isinstance(env_content.get("task_budget"), Mapping)
            else None
        )
        decoded_task_budget = FrameworkTaskBudget.from_hidden_dict(task_budget)
        task_budget_stage = (
            decoded_task_budget.stage.value
            if decoded_task_budget is not None
            else None
        )
        timeout_decision = _resolve_command_timeout(
            timeout,
            task_budget=task_budget,
        )
        working_directory = _resolve_working_directory(cwd)
        command_environment = _resolve_environment(
            env,
            framework_scope=env_content,
        )
        receipt_plan, receipt_effect_source = _terminal_receipt_plan(
            command=str(command),
            potential_plan=execution_plan,
            working_directory=working_directory,
            environment=command_environment,
            environment_overrides=env,
        )
        environment_keys = sorted(
            {
                *(env or {}),
                *(
                    {
                        "AWORLD_TASK_ID",
                        "AWORLD_SESSION_ID",
                        "AWORLD_TASK_EPOCH",
                    }
                    if isinstance(env_content, Mapping)
                    else set()
                ),
            }
        )
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
                    timeout_policy_seconds=timeout_decision.policy_seconds,
                    timeout_policy_override=timeout_decision.policy_override,
                    remaining_task_seconds=timeout_decision.remaining_task_seconds,
                    timeout_limited_by=timeout_decision.limited_by,
                    task_budget_stage=task_budget_stage,
                    safety_check_passed=True,
                    error_type="task_budget_exhausted",
                    environment_keys=environment_keys,
                    terminal_execution_receipt=build_terminal_execution_receipt(
                        code=str(command),
                        plan=receipt_plan,
                        executed=False,
                        exit_code=None,
                        timed_out=False,
                        potential_effect=execution_plan.effect,
                        effect_source=receipt_effect_source,
                        requested_language=language,
                    ),
                ).model_dump(),
            )
            return TextContent(
                type="text",
                text=json.dumps(action_response.model_dump()),
                **{"metadata": {}},
            )
        # Preserve the existing Shell safety boundary for both modes. Raw
        # Python is represented as one safely quoted ``python -c`` command for
        # inspection, but execution below does not round-trip through Shell.
        python_executable = (
            command_environment.get("AWORLD_PYTHON_EXECUTABLE") or sys.executable
        )
        safety_command = (
            command
            if language == "shell"
            else f"{shlex.quote(python_executable)} -c {shlex.quote(command)}"
        )
        is_safe, safety_reason = _check_command_safety(safety_command)
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
                    timeout_policy_seconds=timeout_decision.policy_seconds,
                    timeout_policy_override=timeout_decision.policy_override,
                    remaining_task_seconds=timeout_decision.remaining_task_seconds,
                    timeout_limited_by=timeout_decision.limited_by,
                    task_budget_stage=task_budget_stage,
                    safety_check_passed=False,
                    error_type="security_violation",
                    environment_keys=environment_keys,
                    terminal_execution_receipt=build_terminal_execution_receipt(
                        code=str(command),
                        plan=receipt_plan,
                        executed=False,
                        exit_code=None,
                        timed_out=False,
                        potential_effect=execution_plan.effect,
                        effect_source=receipt_effect_source,
                        requested_language=language,
                    ),
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
        mutation_snapshot = _snapshot_known_write_paths(
            command=str(command),
            plan=execution_plan,
            working_directory=working_directory,
        )
        if receipt_plan.effect == "read_only":
            read_epochs_before = _read_path_epochs(
                plan=receipt_plan,
                working_directory=working_directory,
            )
        start_time = time.time()
        execution_started = True
        result = await _execute_command_async(
            command,
            timeout_decision.effective_seconds,
            cwd=working_directory,
            env=command_environment,
            language=language,
            python_executable=python_executable,
        )
        execution_result = result
        execution_time = time.time() - start_time
        read_epochs_after = (
            _read_path_epochs(
                plan=receipt_plan,
                working_directory=working_directory,
            )
            if result.success and receipt_plan.effect == "read_only"
            else []
        )
        # A replay receipt must bind the bytes just returned to one stable file
        # epoch. If any input changed while the command was reading it, execute
        # normally but do not seed the observation cache.
        read_path_epochs = (
            read_epochs_after
            if read_epochs_before == read_epochs_after
            else []
        )

        # Format output
        formatted_output = _format_command_output(result, output_format)

        # Create metadata
        metadata = TerminalMetadata(
            command=command,
            platform=platform_info["system"],
            working_directory=str(working_directory),
            timeout_seconds=timeout_decision.effective_seconds,
            requested_timeout_seconds=timeout_decision.requested_seconds,
            timeout_policy_seconds=timeout_decision.policy_seconds,
            timeout_policy_override=timeout_decision.policy_override,
            remaining_task_seconds=timeout_decision.remaining_task_seconds,
            timeout_limited_by=timeout_decision.limited_by,
            task_budget_stage=task_budget_stage,
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
            terminal_execution_receipt=build_terminal_execution_receipt(
                code=str(command),
                plan=receipt_plan,
                executed=True,
                exit_code=result.return_code,
                timed_out=result.timed_out,
                capture_complete=result.capture_complete,
                mutation_observed=_mutation_observed_from_snapshot(
                    mutation_snapshot
                ),
                potential_effect=execution_plan.effect,
                effect_source=receipt_effect_source,
                read_path_epochs=read_path_epochs,
                requested_language=language,
            ),
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
                terminal_execution_receipt=build_terminal_execution_receipt(
                    code=str(command),
                    plan=receipt_plan,
                    executed=execution_started,
                    exit_code=(
                        execution_result.return_code
                        if execution_result is not None
                        else None
                    ),
                    timed_out=(
                        execution_result.timed_out
                        if execution_result is not None
                        else False
                    ),
                    capture_complete=(
                        execution_result.capture_complete
                        if execution_result is not None
                        else True
                    ),
                    mutation_observed=(
                        _mutation_observed_from_snapshot(mutation_snapshot)
                        if execution_started
                        else None
                    ),
                    potential_effect=execution_plan.effect,
                    effect_source=receipt_effect_source,
                    requested_language=language,
                ),
            ).model_dump(),
        )
        return TextContent(
            type="text",
            text=json.dumps(
                action_response.model_dump()
            ),  # Empty string instead of None
            **{"metadata": {}},  # Pass as additional fields
        )


class _HeredocRedirects(list):
    """Supply the delimiter quote removal missing from bashlex 0.18.

    The parser has already identified the delimiter token and its redirect.
    Only that token is unquoted; bashlex still reads and delimits the body.
    This per-parser adapter does not change bashlex's process-global state.
    """

    def append(self, item):
        redirect, _ = item
        raw = redirect.output.word
        if "$'" in raw or '$"' in raw:
            raise ValueError(
                "ANSI-C/localized here-document delimiters are unsupported"
            )
        words = shlex.split(raw, posix=True)
        if len(words) != 1 or "\n" in words[0]:
            raise ValueError("Invalid here-document delimiter")
        redirect.heredoc_quoted = words[0] != raw
        redirect.output.word = words[0]
        super().append(item)


class _SafetyShellTokenizer(shell_tokenizer.tokenizer):
    """Treat the unsupported bashlex timing prefix as a command wrapper.

    This changes only tokens recognized as the shell's time keyword, not
    quoted data or here-document bodies. The command following time remains
    visible to the same safety checks through _command_words().
    """

    def token(self):
        token = super().token()
        if token.ttype in {
            shell_tokenizer.tokentype.TIME,
            shell_tokenizer.tokentype.TIMEOPT,
        }:
            token.ttype = shell_tokenizer.tokentype.WORD
            token.flags = shell_utils.typedset(shell_flags.word, token.flags)
        return token


def _shell_child_nodes(node):
    for value in vars(node).values():
        if isinstance(value, shell_ast.node):
            yield value
        elif isinstance(value, list):
            yield from (child for child in value if isinstance(child, shell_ast.node))


def _parse_shell_nodes(command: str):
    """Parse complete input, including commands following a here-document.

    These small adapters use bashlex 0.18's parser interfaces because its
    public parse() does not expose the redirect stack. Keep the dependency
    pinned and exercise delimiter/expansion behavior when upgrading it.
    """

    offset = 0
    while offset < len(command):
        parser = shell_parser._parser(command[offset:], expansionlimit=32)
        parser.tok = _SafetyShellTokenizer(parser.s, parserstate=parser.parserstate)
        parser.redirstack = parser.tok.redirstack = _HeredocRedirects()
        node = parser.parse()
        if node is None:
            break
        shell_ast.posshifter(offset).visit(node)
        pending = [node]
        end = offset
        while pending:
            child = pending.pop()
            end = max(end, child.pos[1])
            pending.extend(_shell_child_nodes(child))
        if end <= offset:
            raise ValueError("Shell parser made no progress")
        yield node
        offset = end + 1


def _heredoc_expansions(body: str):
    """Parse executable expansions in an unquoted here-document.

    Shell quotes in this body are data, even around $(...). bashlex's word
    expander has a here-document mode for that distinction. It does not
    implement nested brace/arithmetic expansion: reject those when we cannot
    inspect them instead of silently losing executable substitutions.
    """

    # Simple brace substitutions contain no nested executable expansion.
    body = re.sub(r"\$\{[^${}`]*\}", "", body)
    if "${" in body:
        raise ValueError(
            "Cannot inspect nested here-document parameter expansion safely"
        )
    # A prefix avoids bashlex's whole-single-quoted-word shortcut: quotes in
    # here-document data never disable its command substitutions.
    text = " " + body
    parser = shell_parser._parser(text, expansionlimit=32)
    token = shell_tokenizer.token(
        shell_tokenizer.tokentype.WORD,
        text,
        (0, len(text)),
        shell_utils.typedset(shell_flags.word),
    )
    parts, _ = shell_subst._expandwordinternal(parser, token, True, True, True, False)
    pending = list(parts)
    while pending:
        node = pending.pop()
        if node.kind == "commandsubstitution":
            source = text[node.pos[0] : node.pos[1]]
            closing = ")" if source.startswith("$(") else "`"
            if not source.endswith(closing):
                raise ValueError("Unterminated here-document command substitution")
        pending.extend(_shell_child_nodes(node))
    return parts


def _shell_reads_stdin(executable: str, args: list[str]) -> bool:
    if executable not in {"sh", "bash", "dash", "ash", "ksh", "zsh"}:
        return False
    index = 0
    while index < len(args):
        value = args[index]
        if value in {"-", "--"}:
            return value == "-" or index + 1 == len(args)
        if not value.startswith("-"):
            return False
        if not value.startswith("--") and "c" in value[1:]:
            return False
        if not value.startswith("--") and "s" in value[1:]:
            return True
        index += 2 if value in {"-o", "-O", "--rcfile", "--init-file"} else 1
    return True


def _has_heredoc(command: str) -> bool:
    """Identify a real redirect token without inspecting its literal body."""

    tokenizer = shell_parser._parser(command).tok
    try:
        for token in tokenizer:
            if token.ttype in {
                shell_tokenizer.tokentype.LESS_LESS,
                shell_tokenizer.tokentype.LESS_LESS_MINUS,
            }:
                return True
    except (bashlex.errors.ParsingError, NotImplementedError, AssertionError):
        # The original lexer will report malformed quoting. Do not impose
        # bashlex's grammar limitations on commands without a here-document.
        pass
    return False


def _shlex_command_segments(command: str) -> list[list[str]]:
    """Keep the existing behavior for shell syntax unrelated to this fix."""

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


def _shell_command_segments(command: str, _depth: int = 0) -> list[list[str]]:
    """Return executable shell words, excluding literal here-document data."""

    if _depth > 16:
        raise ValueError("Shell nesting exceeds the safety inspection limit")
    if not _has_heredoc(command):
        segments = _shlex_command_segments(command)
        compact = "".join(command.split())
        if ":(){:|:&};:" in compact and any(
            _command_words(segment)[0] == ":" for segment in segments
        ):
            raise ValueError("Command contains a shell fork bomb")
        # Here-document shell consumers can themselves invoke sh -c. Check
        # that explicit script without changing ordinary quoted data words.
        if _depth:
            for segment in list(segments):
                executable, args = _command_words(segment)
                if executable in {"sh", "bash", "dash", "ash", "ksh", "zsh"}:
                    for index, arg in enumerate(args):
                        if (
                            arg.startswith("-")
                            and not arg.startswith("--")
                            and "c" in arg[1:]
                        ):
                            if index + 1 < len(args):
                                segments.extend(
                                    _shell_command_segments(args[index + 1], _depth + 1)
                                )
                            break
                        if not arg.startswith("-"):
                            break
        return segments
    segments: list[list[str]] = []
    # Retain nodes as well as their ids: expansion trees are created during
    # traversal and Python may otherwise reuse ids after a tree is released.
    seen = {}

    def visit(node, shell_input=False):
        if id(node) in seen:
            return
        seen[id(node)] = node
        if node.kind == "function" and node.name.word == ":":
            pending = [node.body]
            recursive_pipeline = background = False
            while pending:
                part = pending.pop()
                if part.kind == "operator" and part.op == "&":
                    background = True
                if part.kind == "pipeline":
                    recursive_pipeline = (
                        recursive_pipeline
                        or sum(
                            child.kind == "command"
                            and [
                                word.word for word in child.parts if word.kind == "word"
                            ]
                            == [":"]
                            for child in part.parts
                        )
                        >= 2
                    )
                pending.extend(_shell_child_nodes(part))
            if recursive_pipeline and background:
                raise ValueError("Command contains a shell fork bomb")
        if node.kind == "parameter" and any(
            marker in getattr(node, "value", "") for marker in ("$", "`")
        ):
            raise ValueError("Cannot inspect nested shell parameter expansion safely")
        if node.kind == "command":
            words = [part.word for part in node.parts if part.kind == "word"]
            segments.append(words)
            executable, args = _command_words(words)
            shell_input = shell_input or _shell_reads_stdin(executable, args)
            if executable in {"sh", "bash", "dash", "ash", "ksh", "zsh"}:
                for index, arg in enumerate(args):
                    if (
                        arg.startswith("-")
                        and not arg.startswith("--")
                        and "c" in arg[1:]
                    ):
                        if index + 1 < len(args):
                            segments.extend(
                                _shell_command_segments(args[index + 1], _depth + 1)
                            )
                        break
                    if not arg.startswith("-"):
                        break
        if node.kind == "compound" and getattr(node, "redirects", None):
            # A redirected group such as { bash; } inherits the same stdin as
            # its child commands. Its here-document is code for that child.
            pending = list(getattr(node, "list", []))
            while pending:
                child = pending.pop()
                if child.kind == "command":
                    words = [part.word for part in child.parts if part.kind == "word"]
                    shell_input = shell_input or _shell_reads_stdin(
                        *_command_words(words)
                    )
                if child.kind != "redirect":
                    pending.extend(_shell_child_nodes(child))
        if node.kind == "pipeline":
            # A literal here-document piped into a shell becomes shell code.
            downstream_shell = False
            for part in reversed(node.parts):
                visit(part, downstream_shell)
                if part.kind == "command":
                    words = [item.word for item in part.parts if item.kind == "word"]
                    downstream_shell = downstream_shell or _shell_reads_stdin(
                        *_command_words(words)
                    )
            return
        if node.kind == "redirect" and getattr(node, "heredoc", None) is not None:
            delimiter = node.output.word
            body = node.heredoc.value
            if delimiter:
                body = body[: -len(delimiter)]
            if not getattr(node, "heredoc_quoted", False):
                for expansion in _heredoc_expansions(body):
                    visit(expansion)
            if shell_input:
                segments.extend(_shell_command_segments(body, _depth + 1))
            return
        for child in _shell_child_nodes(node):
            visit(child, shell_input)

    try:
        for root in _parse_shell_nodes(command):
            visit(root)
    except (
        bashlex.errors.ParsingError,
        NotImplementedError,
        RecursionError,
        IndexError,
        AssertionError,
        TypeError,
    ) as exc:
        raise ValueError(str(exc)) from exc
    return segments


def _command_words(segment: list[str]) -> tuple[str, list[str]]:
    """Strip common execution wrappers and return executable plus arguments."""

    words = list(segment)
    while words:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words.pop(0)
            continue
        executable = Path(words[0]).name.lower()
        if executable in {"command", "builtin"}:
            words.pop(0)
            continue
        if executable == "time":
            words.pop(0)
            while words and words[0] in {"-p", "--portability", "--"}:
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


def _terminal_execution_plan(
    command: Any,
    *,
    language: str = "shell",
) -> TerminalExecutionPlan:
    """Analyze exactly what this terminal will hand to its platform shell."""

    if not isinstance(command, str):
        return TerminalExecutionPlan("unknown", "unknown", False, False)
    if language == "python":
        return plan_terminal_execution(command, language="python")
    if language != "shell" or platform_info["system"] == "Windows":
        return TerminalExecutionPlan("unknown", "unknown", False, False)
    try:
        shell_nodes = list(_parse_shell_nodes(command))
    except (
        bashlex.errors.ParsingError,
        NotImplementedError,
        RecursionError,
        ValueError,
        IndexError,
        AssertionError,
        TypeError,
    ):
        # ``bashlex`` cannot parse some otherwise valid quoted heredocs.  The
        # shared receipt parser has a deliberately narrow, non-executing
        # Python-heredoc recognizer, so delegate the parse failure instead of
        # discarding authoritative nested-language evidence here.
        return plan_terminal_execution(command, language="shell")
    return plan_terminal_execution(
        command,
        language="shell",
        shell_nodes=shell_nodes,
        trusted_executable_paths=(sys.executable,),
    )


_CACHE_SAFE_SHELL_BUILTINS = frozenset(
    {":", "cd", "echo", "false", "printf", "pwd", "test", "true", "type"}
)
_SHELL_STARTUP_ENVIRONMENT_KEYS = frozenset(
    {"BASH_ENV", "CDPATH", "ENV", "SHELLOPTS"}
)
_PYTHON_STARTUP_ENVIRONMENT_KEYS = frozenset(
    {"PYTHONHOME", "PYTHONINSPECT", "PYTHONPATH", "PYTHONSTARTUP"}
)


def _command_executable_token(segment: list[str]) -> str | None:
    words = list(segment)
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        words.pop(0)
    return words[0] if words else None


def _path_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _plan_working_directory(
    plan: TerminalExecutionPlan,
    working_directory: Path,
) -> Path | None:
    if not plan.command_cwd_safe:
        return None
    command_cwd = plan.command_cwd
    if command_cwd is None:
        return working_directory.resolve()
    candidate = Path(command_cwd).expanduser()
    if not candidate.is_absolute():
        candidate = working_directory / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def _trusted_read_execution_context(
    *,
    command: str,
    plan: TerminalExecutionPlan,
    working_directory: Path,
    environment: Mapping[str, str],
    environment_overrides: Mapping[str, str] | None,
) -> bool:
    if (
        plan.effect != "read_only"
        or not plan.parsed
        or not plan.read_set_complete
    ):
        return False
    overrides = environment_overrides or {}
    if plan.language == "shell":
        if any(environment.get(key) for key in _SHELL_STARTUP_ENVIRONMENT_KEYS):
            return False
        if any(str(key).startswith("BASH_FUNC_") for key in environment):
            return False
        if any(
            key == "PATH"
            or key in _SHELL_STARTUP_ENVIRONMENT_KEYS
            or str(key).startswith("BASH_FUNC_")
            for key in overrides
        ):
            return False
        if plan.nested_languages and any(
            environment.get(key) for key in _PYTHON_STARTUP_ENVIRONMENT_KEYS
        ):
            return False
        if plan.nested_languages and any(
            key == "AWORLD_PYTHON_EXECUTABLE"
            or key in _PYTHON_STARTUP_ENVIRONMENT_KEYS
            for key in overrides
        ):
            return False
    elif plan.language == "python":
        if any(environment.get(key) for key in _PYTHON_STARTUP_ENVIRONMENT_KEYS):
            return False
        if any(
            key == "AWORLD_PYTHON_EXECUTABLE" or key in _PYTHON_STARTUP_ENVIRONMENT_KEYS
            for key in overrides
        ):
            return False
    else:
        return False

    workspace_root = workspace.resolve()
    resolved_working_directory = _plan_working_directory(plan, working_directory)
    if resolved_working_directory is None:
        return False
    if not _path_within(resolved_working_directory, workspace_root):
        return False
    for raw_path in plan.read_paths:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = resolved_working_directory / candidate
        try:
            resolved_candidate = candidate.resolve()
            stat_result = resolved_candidate.stat()
        except OSError:
            return False
        if not _path_within(resolved_candidate, workspace_root):
            return False
        if not stat_module.S_ISREG(stat_result.st_mode):
            return False

    if plan.language == "python":
        executable = Path(
            environment.get("AWORLD_PYTHON_EXECUTABLE") or sys.executable
        ).expanduser()
        if not executable.is_absolute():
            resolved_text = shutil.which(str(executable), path=environment.get("PATH"))
            if not resolved_text:
                return False
            executable = Path(resolved_text)
        try:
            resolved = executable.resolve()
        except OSError:
            return False
        return (
            not _path_within(resolved, workspace_root)
            and not _path_within(resolved, Path("/tmp").resolve())
            and resolved.is_file()
            and os.access(resolved, os.X_OK)
        )

    if plan.nested_languages == ("python",):
        token_match = re.match(r"\s*(?P<token>[^\s<]+)", command)
        token = token_match.group("token") if token_match is not None else ""
        if not token:
            return False
        if "/" in token:
            executable = Path(token).expanduser()
            if not executable.is_absolute():
                executable = resolved_working_directory / executable
            resolved = executable.resolve()
        else:
            resolved_text = shutil.which(token, path=environment.get("PATH"))
            if not resolved_text:
                return False
            resolved = Path(resolved_text).resolve()
        return (
            not _path_within(resolved, workspace_root)
            and not _path_within(resolved, Path("/tmp").resolve())
            and resolved.is_file()
            and os.access(resolved, os.X_OK)
        )

    try:
        segments = _shell_command_segments(command)
    except ValueError:
        return False
    for segment in segments:
        executable, _ = _command_words(segment)
        if not executable or executable in _CACHE_SAFE_SHELL_BUILTINS:
            continue
        token = _command_executable_token(segment)
        if token is None:
            return False
        if "/" in token:
            candidate = Path(token).expanduser()
            if not candidate.is_absolute():
                candidate = resolved_working_directory / candidate
            resolved = candidate.resolve()
        else:
            resolved_text = shutil.which(token, path=environment.get("PATH"))
            if not resolved_text:
                return False
            resolved = Path(resolved_text).resolve()
        if _path_within(resolved, workspace_root) or _path_within(
            resolved, Path("/tmp").resolve()
        ):
            return False
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return False
    return True


def _read_path_epochs(
    *,
    plan: TerminalExecutionPlan,
    working_directory: Path,
) -> list[dict[str, Any]]:
    epochs: list[dict[str, Any]] = []
    effective_working_directory = _plan_working_directory(plan, working_directory)
    if effective_working_directory is None:
        return []
    for raw_path in plan.read_paths:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = effective_working_directory / candidate
        absolute = Path(os.path.abspath(candidate))
        try:
            link_stat = absolute.lstat()
            resolved = absolute.resolve()
            target_stat = resolved.stat()
        except OSError:
            return []
        if not stat_module.S_ISREG(target_stat.st_mode):
            return []
        epochs.append(
            {
                "path": str(absolute),
                "resolved_path": str(resolved),
                "link_inode": link_stat.st_ino,
                "link_mtime_ns": link_stat.st_mtime_ns,
                "mode": target_stat.st_mode,
                "size": target_stat.st_size,
                "mtime_ns": target_stat.st_mtime_ns,
                "ctime_ns": target_stat.st_ctime_ns,
                "inode": target_stat.st_ino,
            }
        )
    return epochs


def _terminal_receipt_plan(
    *,
    command: str,
    potential_plan: TerminalExecutionPlan,
    working_directory: Path,
    environment: Mapping[str, str],
    environment_overrides: Mapping[str, str] | None,
) -> tuple[TerminalExecutionPlan, str]:
    if potential_plan.effect != "read_only":
        return potential_plan, "parser_contract"
    if _trusted_read_execution_context(
        command=command,
        plan=potential_plan,
        working_directory=working_directory,
        environment=environment,
        environment_overrides=environment_overrides,
    ):
        return potential_plan, "trusted_command_contract"
    return replace(
        potential_plan,
        effect="unknown",
        cacheable=False,
    ), "untrusted_execution_context"


def _path_state(path: Path) -> tuple[Any, ...]:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return ("missing",)
    except OSError as exc:
        return ("error", type(exc).__name__)
    symlink_target: str | None = None
    if path.is_symlink():
        try:
            symlink_target = os.readlink(path)
        except OSError:
            symlink_target = None
    return (
        "present",
        stat_result.st_mode,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
        stat_result.st_ino,
        symlink_target,
    )


def _snapshot_known_write_paths(
    *,
    command: str,
    plan: TerminalExecutionPlan,
    working_directory: Path,
) -> dict[Path, tuple[Any, ...]] | None:
    """Capture cheap pre-execution evidence for literal write targets."""

    if plan.effect != "mutating" or not plan.write_paths:
        return None
    effective_working_directory = _plan_working_directory(plan, working_directory)
    if effective_working_directory is None:
        return None
    snapshot: dict[Path, tuple[Any, ...]] = {}
    for value in plan.write_paths:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = effective_working_directory / candidate
        normalized = Path(os.path.abspath(candidate))
        snapshot[normalized] = _path_state(normalized)
    return snapshot or None


def _mutation_observed_from_snapshot(
    before: dict[Path, tuple[Any, ...]] | None,
) -> bool | None:
    if before is None:
        return None
    observed_change = False
    for path, prior_state in before.items():
        current_state = _path_state(path)
        if "error" in {prior_state[0], current_state[0]}:
            continue
        if current_state != prior_state:
            observed_change = True
    return observed_change


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
    try:
        segments = _shell_command_segments(command)
    except ValueError as exc:
        return False, f"Command could not be parsed safely: {exc}"
    for segment in segments:
        executable, args = _command_words(segment)
        if not executable:
            continue
        if executable == "rm" and _rm_recurses(args):
            target = next(
                (value for value in _rm_targets(args) if _is_broad_rm_target(value)),
                None,
            )
            if target is not None:
                return (
                    False,
                    f"Recursive removal of broad target is not allowed: {target}",
                )
        if executable == "mkfs" or executable.startswith("mkfs."):
            return False, f"Filesystem formatting command is not allowed: {executable}"
        if executable == "diskpart":
            return False, "Disk partitioning command is not allowed"
        if executable == "dd":
            target = _dangerous_device_output(args)
            if target is not None:
                return (
                    False,
                    f"Writing directly to a block device is not allowed: {target}",
                )
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
        if character == "#" and (index == 0 or command[index - 1].isspace()):
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
    raise ValueError("output_format must be one of: structured, markdown, json, text")


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
    language: Literal["shell", "python"] = "shell",
    python_executable: str | None = None,
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
            language == "shell"
            and _is_background_process(command)
            and platform_info["system"] != "Windows"
        )
        process_options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "limit": _STREAM_READ_CHUNK_BYTES,
            "cwd": str(cwd or workspace),
            "env": dict(env) if env is not None else None,
        }
        if language == "shell":
            process_options["shell"] = True
            if platform_info["system"] != "Windows":
                process_options.update(
                    executable="/bin/bash",
                    start_new_session=True,
                )
            process = await asyncio.create_subprocess_shell(command, **process_options)
        else:
            if platform_info["system"] != "Windows":
                process_options["start_new_session"] = True
            process = await asyncio.create_subprocess_exec(
                python_executable or sys.executable,
                "-c",
                command,
                **process_options,
            )
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
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
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
