import asyncio
from collections import deque
import json
import logging
import platform
import signal
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Union
import os
import re

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

# Keep strong references to drain-only tasks for background children that retain
# inherited stdout/stderr descriptors after their launching shell has exited.
# Each task drops all captured bytes before being registered here.
_background_drain_tasks: set[asyncio.Task[None]] = set()

# Define dangerous commands for safety
dangerous_commands = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",  # Unix
    "del /f /s /q",
    # "format",
    # "format",
    "diskpart",  # Windows
    "sudo rm",
    "sudo dd",
    "sudo mkfs",  # Sudo variants
]

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


class TerminalMetadata(BaseModel):
    """Metadata for terminal operation results."""

    command: str
    platform: str
    working_directory: str
    timeout_seconds: int
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


class _BoundedStreamCapture:
    """Incrementally retain a byte-bounded head and tail of one pipe.

    Once the limit is crossed, all later bytes are still read from the pipe to
    avoid subprocess backpressure, but only the fixed-size head/tail window is
    retained.  No command output is spooled to disk.
    """

    def __init__(self, stream_name: str, max_bytes: int) -> None:
        self.stream_name = stream_name
        self.max_bytes = max(1, int(max_bytes))
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
    timeout: int = Field(
        default=_DEFAULT_COMMAND_TIMEOUT_SECONDS,
        description="Command timeout in seconds (default: 300, max: 3600)",
    ),
    output_format: str = Field(
        default="markdown", description="Output format: 'markdown', 'json', or 'text'"
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

    # Timeout: env TERMINAL_TIMEOUT overrides parameter/default when set
    env_timeout = os.environ.get("TERMINAL_TIMEOUT")
    if env_timeout is not None:
        try:
            timeout = int(env_timeout)
        except ValueError:
            pass  # keep current timeout if env value is not a valid integer
    timeout = max(1, min(int(timeout), _MAX_COMMAND_TIMEOUT_SECONDS))

    try:
        # Safety check
        is_safe, safety_reason = _check_command_safety(command)
        if not is_safe:
            action_response = ActionResponse(
                success=False,
                message=f"Command rejected for security reasons: {safety_reason}",
                metadata=TerminalMetadata(
                    command=command,
                    platform=platform_info["system"],
                    working_directory=str(workspace),
                    timeout_seconds=timeout,
                    safety_check_passed=False,
                    error_type="security_violation",
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
        result = await _execute_command_async(command, timeout)
        execution_time = time.time() - start_time

        # Format output
        formatted_output = _format_command_output(result, output_format)

        # Create metadata
        metadata = TerminalMetadata(
            command=command,
            platform=platform_info["system"],
            working_directory=str(workspace),
            timeout_seconds=timeout,
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
                working_directory=str(workspace),
                timeout_seconds=timeout,
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


def _check_command_safety(command: str) -> tuple[bool, str | None]:
    """Check if command is safe to execute.

    Args:
        command: Command string to check

    Returns:
        Tuple of (is_safe, reason_if_unsafe)
    """
    command_lower = command.lower().strip()

    for dangerous_cmd in dangerous_commands:
        if dangerous_cmd.lower() in command_lower:
            return False, f"Command contains dangerous pattern: {dangerous_cmd}"

    return True, None


# Match background-execution ampersand '&', while excluding 2>&1, &&, &>, etc.
# - `\s+&` ensures there is a space before '&' (excludes 2>&1, &>)
# - `(?!\s*&)` ensures the '&' is not followed by another '&' (excludes &&)
# This matches anywhere in the line, including patterns like
#   "cmd1 & cmd2" or "cmd & echo done".
_BACKGROUND_AMPERSAND_RE = re.compile(r"\s+&(?!\s*&)")


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
    if _BACKGROUND_AMPERSAND_RE.search(cmd_stripped):
        return True
    cmd_lower = cmd_stripped.lower()
    for keyword in LONG_RUNNING_KEYWORDS:
        if keyword.lower() in cmd_lower:
            return True
    return False


def _format_command_output(
    result: CommandResult, output_format: str = "markdown"
) -> str:
    """Format command execution results for LLM consumption.

    Args:
        result: Command execution result
        output_format: Format type ('markdown', 'json', 'text')

    Returns:
        Formatted string suitable for LLM consumption
    """
    stdout = _bounded_inline_stream(result.stdout)
    stderr = _bounded_inline_stream(result.stderr)
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

    else:  # markdown (default)
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


async def _execute_command_async(command: str, timeout: int) -> CommandResult:
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
    stdout_capture = _BoundedStreamCapture("stdout", (capture_limit + 1) // 2)
    stderr_capture = _BoundedStreamCapture("stderr", capture_limit // 2)
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
            _track_background_drain_tasks(pending_tasks)

        if timed_out:
            timeout_message = f"Command timed out after {timeout} seconds"
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


if __name__ == "__main__":
    import sys

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
