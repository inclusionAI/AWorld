"""Local physical sandbox: working directory, filesystem and POSIX processes.

This is a local execution carrier; it does not provide OS access isolation.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import stat
import tempfile

MAX_BYTES = 50 * 1024
MAX_LINES = 2000


async def _file_operation(function, *args):
    # Threads cannot be killed. Finish the admitted file operation before the
    # run releases ownership, even if its caller is cancelled.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


class LocalSandbox:
    """Captured cwd, no process-wide chdir, container, installer, or shell profile."""

    def __init__(self, cwd: str | Path | None = None) -> None:
        self.cwd = Path.cwd() if cwd is None else Path(cwd).expanduser().resolve()
        if not self.cwd.is_dir():
            raise NotADirectoryError(str(self.cwd))

    def _path(self, path: str) -> Path:
        target = Path(path).expanduser()
        return (target if target.is_absolute() else self.cwd / target).resolve()

    async def read(self, path: str, offset: int, limit: int) -> dict:
        return await _file_operation(self._read, self._path(path), offset, limit)

    @staticmethod
    def _read(path: Path, offset: int, limit: int) -> dict:
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("read requires a regular file")
        output = bytearray()
        count, partial, more = 0, False, False
        with path.open("rb") as stream:
            # Bounded allocations even when skipping a very long line.
            for _ in range(offset - 1):
                line = stream.readline(MAX_BYTES + 1)
                if not line:
                    raise ValueError("offset is beyond end of file")
                while line and not line.endswith(b"\n"):
                    line = stream.readline(MAX_BYTES + 1)
            for _ in range(min(limit, MAX_LINES)):
                line = stream.readline(MAX_BYTES + 1)
                if not line:
                    break
                if b"\0" in line:
                    raise ValueError("read supports UTF-8 text, not binary files")
                remaining = MAX_BYTES - len(output)
                if len(line) > remaining:
                    if not output:
                        output.extend(line[:remaining])
                        partial = True
                    more = True
                    break
                output.extend(line)
                count += 1
            else:
                more = bool(stream.read(1))
        if count == 0 and not output and offset > 1:
            raise ValueError("offset is beyond end of file")
        # Remove a byte-cap boundary inside a UTF-8 code point, but reject
        # invalid UTF-8 elsewhere instead of silently displaying binary data.
        raw = bytes(output)
        if partial:
            for trim in range(4):
                try:
                    content = (raw[:-trim] if trim else raw).decode("utf-8")
                    break
                except UnicodeDecodeError as exc:
                    if exc.start < len(raw) - 4:
                        raise
            else:
                raise ValueError("read supports UTF-8 text")
        else:
            content = raw.decode("utf-8")
        return {"path": str(path), "content": content, "offset": offset,
                "lines": count, "truncated": more, "line_truncated": partial,
                "next_offset": (offset + count) if more else None,
                "hint": "Line exceeds byte limit; use bash for a byte range." if partial else None}

    async def write(self, path: str, content: str) -> dict:
        return await _file_operation(self._write, self._path(path), content)

    @staticmethod
    def _write(path: Path, content: str) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = content.encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=".aworld-write-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
            if path.exists():
                os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"path": str(path), "bytes_written": len(payload)}

    async def bash(self, command: str, timeout: float) -> dict:
        if os.name != "posix":
            raise NotImplementedError("Local bash backend currently requires POSIX")
        # Shield spawning too: cancellation must not orphan a just-created process.
        descriptor, log_path = tempfile.mkstemp(prefix="aworld-bash-", suffix=".log")
        log = os.fdopen(descriptor, "wb")
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            "/bin/bash", "--noprofile", "--norc", "-c", command,
            cwd=str(self.cwd), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        ))
        process = None
        drain = None
        completion = None
        tail = bytearray()
        total = 0

        async def collect():
            nonlocal total
            while True:
                chunk = await process.stdout.read(8192)
                if not chunk:
                    break
                log.write(chunk)
                total += len(chunk)
                tail.extend(chunk)
                if len(tail) > MAX_BYTES:
                    del tail[:-MAX_BYTES]

        def kill_group():
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        timed_out = False
        try:
            process = await asyncio.shield(spawning)
            drain = asyncio.create_task(collect())
            async def finish():
                await drain
                await process.wait()
            completion = asyncio.create_task(finish())
            try:
                await asyncio.wait_for(asyncio.shield(completion), timeout)
            except asyncio.TimeoutError:
                timed_out = True
                kill_group()
                await completion
            text = tail.decode("utf-8", errors="replace")
            lines = text.splitlines(keepends=True)
            truncated = total > MAX_BYTES or len(lines) > MAX_LINES
            return {"output": "".join(lines[-MAX_LINES:]), "exit_code": process.returncode,
                    "timed_out": timed_out, "truncated": truncated,
                    "full_output_path": log_path if truncated else None}
        finally:
            try:
                if process is None:
                    process = await spawning
                kill_group()
                await process.wait()
                if completion is not None:
                    await completion
            finally:
                log.close()
                if total <= MAX_BYTES and len(tail.splitlines()) <= MAX_LINES:
                    os.unlink(log_path)
