"""Default read/write/bash tools; execution belongs to their physical sandbox."""

from __future__ import annotations

import math
from pathlib import Path

from aworld.core.sandbox import Sandbox, LocalSandbox
from aworld.core.sandbox.local import MAX_LINES
from .function import Tool, ToolExecutionError
from .sessions import session_tools


def _text(arguments, key, *, allow_empty=False):
    value = arguments.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{key} must be {'text' if allow_empty else 'non-empty text'}")
    return value


def _integer(arguments, key, default):
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def default_tools(cwd: str | Path | None = None, *, sandbox: Sandbox | None = None) -> tuple[Tool, ...]:
    """Return ordinary read/write/bash tools. The Agent never sees the backend.

    Relative paths are sandbox-relative; absolute paths are accepted. Local cwd
    is an execution location, not a filesystem restriction.
    """
    if sandbox is not None and cwd is not None:
        raise ValueError("Configure cwd on the supplied sandbox")
    host = LocalSandbox(cwd) if sandbox is None else sandbox
    if not all(callable(getattr(host, name, None)) for name in ("read", "write", "bash")):
        raise TypeError("sandbox must implement async read/write/bash")

    async def read(arguments, context):
        return await host.read(_text(arguments, "path"), _integer(arguments, "offset", 1),
                               _integer(arguments, "limit", MAX_LINES))

    async def write(arguments, context):
        return await host.write(_text(arguments, "path"), _text(arguments, "content", allow_empty=True))

    async def bash(arguments, context):
        command = _text(arguments, "command")
        timeout = arguments.get("timeout", 120)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive finite seconds")
        result = await host.bash(command, timeout)
        if isinstance(result, dict) and (result.get("timed_out") or result.get("exit_code", 0) != 0):
            raise ToolExecutionError("bash timed out or exited unsuccessfully", result)
        return result

    def schema(properties, required):
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

    return (
        Tool("read", "Read UTF-8 text. offset starts at 1; output capped at 2000 lines/50 KiB. Continue with next_offset.",
             schema({"path": {"type": "string"}, "offset": {"type": "integer", "minimum": 1},
                     "limit": {"type": "integer", "minimum": 1}}, ["path"]), read),
        Tool("write", "Create or overwrite an entire UTF-8 file, creating parent directories.",
             schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]), write),
        Tool("bash", "Run bash in the backend working directory. Returns exit_code and combined output tail. Default timeout 120 seconds.",
             schema({"command": {"type": "string"}, "timeout": {"type": "number", "exclusiveMinimum": 0}}, ["command"]), bash),
        *session_tools(),
    )
