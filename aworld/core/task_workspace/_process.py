"""Bounded, cancellable process execution without shell interpolation."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ProcessLimits:
    timeout_seconds: float = 30.0
    output_bytes: int = 65536
    cpu_seconds: int = 20
    memory_bytes: int = 1024 * 1024 * 1024
    file_bytes: int = 16 * 1024 * 1024
    open_files: int = 128

    def __post_init__(self):
        for key, value in asdict(self).items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{key} must be a positive finite operation bound")
            if key != "timeout_seconds" and not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
        if self.output_bytes < 64:
            raise ValueError("output_bytes must be at least 64")


def isolated_env(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Only explicit task environment plus minimal OS execution defaults."""
    keys = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "WINDIR"}
    result = {key: value for key, value in os.environ.items() if key in keys}
    result.setdefault("PATH", os.defpath)
    if overrides:
        for key, value in overrides.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or "\0" in key + value
                or "=" in key
            ):
                raise ValueError(
                    "process environment requires ordinary string key/value pairs"
                )
            result[key] = value
    return result


async def _capture(stream, limit: int) -> dict:
    digest = hashlib.sha256()
    prefix, tail = bytearray(), bytearray()
    total = 0
    first = limit // 2
    while True:
        data = await stream.read(65536)
        if not data:
            break
        digest.update(data)
        total += len(data)
        if len(prefix) < first:
            taken = min(first - len(prefix), len(data))
            prefix.extend(data[:taken])
            data = data[taken:]
        tail.extend(data)
        if len(tail) > limit - first:
            del tail[: len(tail) - (limit - first)]
    content = (
        bytes(prefix)
        + (b"\n...[truncated]...\n" if total > limit else b"")
        + bytes(tail)
    )
    return {
        "text": content.decode("utf-8", errors="replace"),
        "bytes": total,
        "sha256": digest.hexdigest(),
        "truncated": total > limit,
    }


async def _terminate(process) -> None:
    # Also reap ordinary descendants which outlive the leader and hold pipes.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif process.returncode is None:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 0.5)
    except asyncio.TimeoutError:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.returncode is None:
        process.kill()
    await process.wait()


async def run_bounded(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    limits: ProcessLimits | None = None,
) -> dict:
    limits = limits or ProcessLimits()
    if not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv):
        raise ValueError("argv must be a nonempty list of NUL-free strings")
    cwd = Path(cwd).resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError("working directory must exist")
    environment = isolated_env(env)
    started = time.monotonic()
    child = Path(__file__).with_name("_process_child.py")
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(child),
            json.dumps(asdict(limits)),
            *argv,
            cwd=str(cwd),
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    )
    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        process = await spawn
        await _terminate(process)
        raise
    streams = [
        asyncio.create_task(_capture(process.stdout, limits.output_bytes)),
        asyncio.create_task(_capture(process.stderr, limits.output_bytes)),
    ]
    timed_out = False
    try:
        try:
            await asyncio.wait_for(process.wait(), limits.timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
        await _terminate(process)
        captured = await asyncio.wait_for(asyncio.gather(*streams), 2)
    except BaseException:
        await asyncio.shield(_terminate(process))
        for task in streams:
            task.cancel()
        await asyncio.gather(*streams, return_exceptions=True)
        raise
    return {
        "argv": list(argv),
        "cwd": str(cwd),
        "return_code": process.returncode,
        "timed_out": timed_out,
        "stdout": captured[0],
        "stderr": captured[1],
        "elapsed_seconds": time.monotonic() - started,
        "environment_sha256": hashlib.sha256(
            json.dumps(environment, sort_keys=True).encode()
        ).hexdigest(),
        "environment_keys": sorted(environment),
        "limits": asdict(limits),
        "resource_capabilities": {
            "cpu": os.name == "posix",
            "file_size": os.name == "posix",
            "open_files": os.name == "posix",
            "memory": sys.platform.startswith("linux"),
        },
    }
