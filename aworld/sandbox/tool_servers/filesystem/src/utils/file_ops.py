"""File operation helper functions.

Read and search helpers in this module enforce limits while producing data.  The
MCP/model output boundary is deliberately not treated as a memory-safety boundary:
by the time that boundary runs, this process has already materialized the result.
"""

from __future__ import annotations

import asyncio
import base64
import codecs
from dataclasses import asdict, dataclass
from collections import deque
from fnmatch import fnmatch
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Optional
from difflib import unified_diff

try:  # Package import (tests / installed wheel).
    from .limits import FilesystemLimits, truncation_notice
except ImportError:  # Direct script import used by the stdio MCP server.
    from limits import FilesystemLimits, truncation_notice

# Number of bytes to sample when detecting whether a file is text
TEXT_SAMPLE_SIZE = 8192


@dataclass(frozen=True)
class BoundedTextRead:
    """Text plus the producer-side completeness metadata for a read."""

    content: str
    complete: bool
    returned_bytes: int
    total_bytes: int
    truncation_reason: Optional[str] = None
    next_line: Optional[int] = None


@dataclass(frozen=True)
class BoundedBinaryRead:
    """One bounded binary chunk."""

    data: bytes
    offset: int
    next_offset: int
    total_bytes: int
    complete: bool


@dataclass(frozen=True)
class BoundedSearchResult:
    """Plain-text compatible search output with useful completeness metadata."""

    text: str
    complete: bool
    matches: int
    files_scanned: int
    bytes_scanned: int
    truncation_reason: Optional[str] = None
    timed_out: bool = False


@dataclass(frozen=True)
class BoundedTraversalResult:
    """Bounded recursive path search output."""

    paths: tuple[str, ...]
    complete: bool
    entries_scanned: int
    truncation_reason: Optional[str] = None


def format_size(bytes: int) -> str:
    """格式化文件大小"""
    units = ["B", "KB", "MB", "GB", "TB"]
    if bytes == 0:
        return "0 B"
    
    i = 0
    size = float(bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    
    return f"{size:.2f} {units[i]}"


async def read_file(path: str) -> str:
    """Read a safely bounded UTF-8 prefix (legacy string-only helper)."""
    return (await read_text_bounded(path)).content


def _read_file_sync(path: str) -> str:
    """Synchronously read file content as UTF-8 text."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _decode_utf8_prefix(data: bytes, *, complete: bool) -> tuple[str, int]:
    """Decode a bounded prefix without replacing or splitting a UTF-8 character."""

    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    text = decoder.decode(data, final=complete)
    buffered, _ = decoder.getstate()
    return text, len(data) - len(buffered)


def _decode_utf8_suffix(data: bytes) -> tuple[str, int]:
    """Decode bytes read from a file tail, trimming only a split leading codepoint."""

    for skipped in range(min(4, len(data) + 1)):
        try:
            return data[skipped:].decode("utf-8"), skipped
        except UnicodeDecodeError as exc:
            if exc.start != 0:
                raise
    return "", len(data)


def _read_physical_line(
    stream,
    *,
    max_line_bytes: int,
    remaining_scan_bytes: int,
) -> tuple[bytes, int, bool, bool]:
    """Read one physical line with fixed memory.

    Returns ``(retained, scanned, line_truncated, reached_scan_limit)``.  When a
    line exceeds ``max_line_bytes`` its remainder is drained in bounded chunks so
    the next call still begins on a real line boundary.
    """

    if remaining_scan_bytes <= 0:
        return b"", 0, False, True
    first_limit = min(max_line_bytes + 1, remaining_scan_bytes)
    part = stream.readline(first_limit)
    if not part:
        return b"", 0, False, False

    scanned = len(part)
    line_truncated = len(part) > max_line_bytes
    retained = part[:max_line_bytes]
    ended = part.endswith(b"\n")

    while not ended and line_truncated and scanned < remaining_scan_bytes:
        chunk = stream.readline(min(64 * 1024, remaining_scan_bytes - scanned))
        if not chunk:
            ended = True
            break
        scanned += len(chunk)
        ended = chunk.endswith(b"\n")

    reached_scan_limit = not ended and scanned >= remaining_scan_bytes
    if line_truncated and ended and retained and not retained.endswith(b"\n"):
        # Preserve a line boundary without retaining the arbitrarily large body.
        retained = retained[:-1] + b"\n"
    return retained, scanned, line_truncated, reached_scan_limit


def _read_text_forward_sync(
    path: str,
    *,
    start_line: int,
    end_line: Optional[int],
    formatting: str,
    limits: FilesystemLimits,
) -> BoundedTextRead:
    if start_line < 1:
        raise ValueError("line numbers must be positive (1-based)")
    if end_line is not None and end_line < start_line:
        raise ValueError("end line must be >= start line")

    total_bytes = os.path.getsize(path)
    selected: list[bytes] = []
    selected_bytes = 0
    scanned_bytes = 0
    line_no = 0
    complete = True
    reason: Optional[str] = None
    next_line: Optional[int] = None

    with open(path, "rb") as stream:
        while True:
            if scanned_bytes >= limits.max_scan_bytes:
                complete = False
                reason = "scan_bytes"
                next_line = line_no + 1
                break
            if len(selected) >= limits.max_result_lines:
                complete = False
                reason = "result_lines"
                next_line = line_no + 1
                break

            raw_line, scanned, line_truncated, scan_exhausted = _read_physical_line(
                stream,
                max_line_bytes=limits.max_line_bytes,
                remaining_scan_bytes=limits.max_scan_bytes - scanned_bytes,
            )
            scanned_bytes += scanned
            if scanned == 0:
                break
            line_no += 1

            if line_no >= start_line:
                remaining = limits.max_read_bytes - selected_bytes
                if remaining <= 0:
                    complete = False
                    reason = "read_bytes"
                    next_line = line_no
                    break
                retained = raw_line[:remaining]
                selected.append(retained)
                selected_bytes += len(retained)
                if len(retained) < len(raw_line):
                    complete = False
                    reason = "read_bytes"
                    next_line = line_no
                    break
                if line_truncated:
                    complete = False
                    reason = reason or "line_bytes"

            if scan_exhausted:
                complete = False
                reason = reason or "scan_bytes"
                next_line = line_no
                break
            if end_line is not None and line_no >= end_line:
                break

    if end_line is not None and line_no < end_line and scanned_bytes < total_bytes:
        complete = False
        reason = reason or "scan_bytes"
        next_line = line_no + 1
    elif end_line is None and scanned_bytes < total_bytes:
        complete = False
        reason = reason or "read_bytes"
        next_line = next_line or line_no + 1

    if formatting == "head":
        raw = b"\n".join(line.rstrip(b"\n\r") for line in selected)
    elif formatting == "range":
        raw = b"".join(selected).rstrip(b"\n\r")
    else:
        raw = b"".join(selected)

    text, consumed = _decode_utf8_prefix(raw, complete=complete)
    if consumed != len(raw):
        complete = False
        reason = reason or "utf8_boundary"
    return BoundedTextRead(
        content=text,
        complete=complete,
        returned_bytes=consumed,
        total_bytes=total_bytes,
        truncation_reason=reason,
        next_line=next_line,
    )


def _tail_text_sync(path: str, num_lines: int, limits: FilesystemLimits) -> BoundedTextRead:
    if num_lines < 1:
        raise ValueError("tail must be a positive line count")

    total_bytes = os.path.getsize(path)
    requested_lines = min(num_lines, limits.max_result_lines)
    reason: Optional[str] = "result_lines" if requested_lines != num_lines else None
    position = total_bytes
    scanned = 0
    chunks: deque[bytes] = deque()
    collected_bytes = 0
    newline_count = 0
    file_ends_newline = False

    with open(path, "rb") as stream:
        if total_bytes:
            stream.seek(total_bytes - 1)
            file_ends_newline = stream.read(1) == b"\n"
        required_newlines = requested_lines + (1 if file_ends_newline else 0)
        while position > 0 and scanned < limits.max_scan_bytes:
            chunk_size = min(64 * 1024, position, limits.max_scan_bytes - scanned)
            position -= chunk_size
            stream.seek(position)
            chunk = stream.read(chunk_size)
            scanned += len(chunk)
            chunks.appendleft(chunk)
            collected_bytes += len(chunk)
            newline_count += chunk.count(b"\n")
            if newline_count >= required_newlines:
                break
            if collected_bytes >= limits.max_read_bytes:
                reason = reason or "read_bytes"
                break

    lines = b"".join(chunks).splitlines(keepends=True)
    selected = b"".join(lines[-requested_lines:]) if requested_lines else b""
    if len(selected) > limits.max_read_bytes:
        selected = selected[-limits.max_read_bytes :]
        reason = reason or "read_bytes"
    if position > 0 and len(lines) < requested_lines + (1 if file_ends_newline else 0):
        reason = reason or ("scan_bytes" if scanned >= limits.max_scan_bytes else "read_bytes")

    complete = reason is None
    text, skipped = _decode_utf8_suffix(selected)
    if skipped:
        complete = False
        reason = reason or "utf8_boundary"
    return BoundedTextRead(
        content=text,
        complete=complete,
        returned_bytes=len(selected) - skipped,
        total_bytes=total_bytes,
        truncation_reason=reason,
        next_line=None,
    )


async def read_text_bounded(
    path: str,
    *,
    head: Optional[int] = None,
    tail: Optional[int] = None,
    limits: Optional[FilesystemLimits] = None,
) -> BoundedTextRead:
    """Read text with line semantics compatible with the filesystem MCP tool."""

    limits = limits or FilesystemLimits.from_env()
    if head is not None and head < 1:
        raise ValueError("head must be a positive line number/count")
    if tail is not None and tail < 1:
        raise ValueError("tail must be a positive line number/count")
    if head is not None and tail is not None:
        if head > tail:
            raise ValueError("head must be <= tail when both are specified")
        args = (path,)
        kwargs = {
            "start_line": head,
            "end_line": tail,
            "formatting": "range",
            "limits": limits,
        }
        return await asyncio.to_thread(_read_text_forward_sync, *args, **kwargs)
    if tail is not None:
        return await asyncio.to_thread(_tail_text_sync, path, tail, limits)
    return await asyncio.to_thread(
        _read_text_forward_sync,
        path,
        start_line=1,
        end_line=head,
        formatting="head" if head is not None else "full",
        limits=limits,
    )


def _read_binary_chunk_sync(
    path: str,
    offset: int,
    requested_limit: Optional[int],
    hard_limit: int,
) -> BoundedBinaryRead:
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if requested_limit is not None and requested_limit < 1:
        raise ValueError("limit must be positive")
    effective_limit = hard_limit if requested_limit is None else min(requested_limit, hard_limit)
    total_bytes = os.path.getsize(path)
    with open(path, "rb") as stream:
        stream.seek(min(offset, total_bytes))
        data = stream.read(effective_limit)
    next_offset = min(offset, total_bytes) + len(data)
    return BoundedBinaryRead(
        data=data,
        offset=offset,
        next_offset=next_offset,
        total_bytes=total_bytes,
        complete=next_offset >= total_bytes,
    )


async def read_binary_chunk(
    path: str,
    *,
    offset: int = 0,
    limit: Optional[int] = None,
    hard_limit: Optional[int] = None,
) -> BoundedBinaryRead:
    limits = FilesystemLimits.from_env()
    return await asyncio.to_thread(
        _read_binary_chunk_sync,
        path,
        offset,
        limit,
        hard_limit or limits.max_binary_bytes,
    )


async def write_file(path: str, content: str) -> None:
    """Atomically write text content to a file."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write_file_sync, path, content)


def _write_file_sync(path: str, content: str) -> None:
    """Synchronously write text content to a file using a temporary file + replace."""
    dir_path = Path(path).parent
    dir_path.mkdir(parents=True, exist_ok=True)
    
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=dir_path, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        Path(tmp_path).replace(path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


async def write_file_base64(path: str, content_base64: str) -> None:
    """Atomically write base64-decoded bytes to a file."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write_file_base64_sync, path, content_base64)


def _write_file_base64_sync(path: str, content_base64: str) -> None:
    """Decode base64 incrementally and atomically replace the destination."""
    dir_path = Path(path).parent
    dir_path.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(mode="wb", dir=dir_path, delete=False) as tmp:
        tmp_path = tmp.name
        # A multiple of four keeps each chunk independently decodable while
        # avoiding a second full-size decoded object in memory.
        chunk_chars = 1024 * 1024
        try:
            for start in range(0, len(content_base64), chunk_chars):
                encoded = content_base64[start : start + chunk_chars]
                if "=" in encoded and start + chunk_chars < len(content_base64):
                    raise ValueError("Invalid base64: padding before end of input")
                tmp.write(base64.b64decode(encoded, validate=True))
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    try:
        Path(tmp_path).replace(path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


async def head_file(path: str, num_lines: int) -> str:
    """Read the first N lines of a text file."""
    return (await read_text_bounded(path, head=num_lines)).content


def _head_file_sync(path: str, num_lines: int) -> str:
    """Synchronously read the first N lines using producer-side budgets."""
    return _read_text_forward_sync(
        path,
        start_line=1,
        end_line=num_lines,
        formatting="head",
        limits=FilesystemLimits.from_env(),
    ).content


async def tail_file(path: str, num_lines: int) -> str:
    """Read the last N lines of a text file."""
    return (await read_text_bounded(path, tail=num_lines)).content


def _tail_file_sync(path: str, num_lines: int) -> str:
    """Synchronously read the last N lines using reverse bounded I/O."""
    return _tail_text_sync(path, num_lines, FilesystemLimits.from_env()).content


def _is_text_file_sync(path: str) -> bool:
    """Heuristically detect if a file is text by sampling bytes and trying UTF-8 decode."""
    try:
        with open(path, "rb") as f:
            sample = f.read(TEXT_SAMPLE_SIZE)
        if not sample:
            return True
        sample.decode("utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


async def is_text_file(path: str) -> bool:
    """Asynchronously detect whether a file is text based on content."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _is_text_file_sync, path)


def _read_file_lines_sync(path: str, start_1based: int, end_1based: int) -> str:
    """Synchronously read lines [start, end] with bounded memory."""
    return _read_text_forward_sync(
        path,
        start_line=start_1based,
        end_line=end_1based,
        formatting="range",
        limits=FilesystemLimits.from_env(),
    ).content


async def read_file_lines(path: str, start_1based: int, end_1based: int) -> str:
    """Read lines [start, end] (1-based inclusive) from a text file."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _read_file_lines_sync, path, start_1based, end_1based
    )


def _read_file_binary_sync(path: str) -> bytes:
    """Synchronously read one safely bounded binary chunk."""
    limits = FilesystemLimits.from_env()
    return _read_binary_chunk_sync(path, 0, None, limits.max_binary_bytes).data


async def read_file_binary(path: str) -> bytes:
    """Read one safely bounded binary chunk."""
    return (await read_binary_chunk(path)).data


def get_mime_and_filename(path: str) -> tuple[str, str]:
    """Return (mime_type, filename) for a given path."""
    mime_type, _ = mimetypes.guess_type(path)
    if not mime_type:
        mime_type = "application/octet-stream"
    filename = Path(path).name
    return mime_type, filename


def _apply_edits_range_sync(path: str, start: int, end: int, new_content: str) -> None:
    """Synchronously replace content[start:end] with new_content; start==end inserts; empty new_content deletes."""
    max_edit_bytes = FilesystemLimits.from_env().max_edit_bytes
    file_size = os.path.getsize(path)
    if file_size > max_edit_bytes:
        raise ValueError(
            f"File is too large for an atomic edit: {file_size} bytes; limit={max_edit_bytes}"
        )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if start < 0 or end < start or start > len(content) or end > len(content):
        raise ValueError(f"Invalid range: start={start}, end={end}, file length={len(content)}")
    new_text = content[:start] + new_content + content[end:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)


async def apply_edits_range(path: str, start: int, end: int, new_content: str) -> None:
    """Replace file content [start, end) with new_content; start==end inserts; empty new_content deletes."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _apply_edits_range_sync, path, start, end, new_content
    )


def _copy_source_to_staged_sync(
    source: str, staged_target: str, *, max_copy_bytes: int
) -> int:
    """Copy from a freshly validated fd into a parent-owned staging file."""

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    # Avoid a blocking open if a previously validated path is replaced by a
    # FIFO or other special file before the worker opens it.
    flags |= getattr(os, "O_NONBLOCK", 0)
    source_descriptor = os.open(source, flags)
    try:
        source_stat = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError(f"Path is not a regular file: {source}")
        if source_stat.st_size > max_copy_bytes:
            raise ValueError(
                f"File is too large to copy safely: {source_stat.st_size} bytes; "
                f"limit={max_copy_bytes}"
            )
        copied = 0
        with os.fdopen(source_descriptor, "rb", closefd=False) as source_stream:
            with open(staged_target, "wb") as target_stream:
                while True:
                    chunk = source_stream.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > max_copy_bytes:
                        raise ValueError(
                            "File grew beyond the safe copy limit: "
                            f"{copied} bytes; limit={max_copy_bytes}"
                        )
                    target_stream.write(chunk)
                target_stream.flush()
                os.fsync(target_stream.fileno())
        return copied
    finally:
        os.close(source_descriptor)


async def _kill_async_worker(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    await process.communicate()


async def copy_file_binary(source: str, target: str) -> None:
    """Copy in a killable worker, publishing the target only after success."""
    limits = FilesystemLimits.from_env()
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target_path.parent,
        prefix=f".{target_path.name}.aworld-copy-",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        staged_target = Path(temporary.name)
    payload = {
        "source": source,
        "staged_target": str(staged_target),
        "max_copy_bytes": limits.max_copy_bytes,
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).resolve()),
        "--copy-worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=(os.name == "posix"),
    )
    try:
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(payload).encode("utf-8")),
                timeout=limits.copy_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            await _kill_async_worker(process)
            raise TimeoutError(
                f"File copy timed out after {limits.copy_timeout_seconds:g}s"
            ) from exc
        except BaseException:
            await asyncio.shield(_kill_async_worker(process))
            raise
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "filesystem copy worker failed")
        try:
            decoded = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("filesystem copy worker returned invalid output") from exc
        if "error" in decoded:
            if decoded.get("error_type") == "ValueError":
                raise ValueError(decoded["error"])
            raise RuntimeError(decoded["error"])
        copied = decoded.get("copied")
        if (
            isinstance(copied, bool)
            or not isinstance(copied, int)
            or copied < 0
            or copied > limits.max_copy_bytes
            or staged_target.stat().st_size != copied
        ):
            raise RuntimeError("filesystem copy worker returned invalid metadata")
        os.replace(staged_target, target_path)
    finally:
        staged_target.unlink(missing_ok=True)


def _copy_worker_main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        copied = _copy_source_to_staged_sync(
            payload["source"],
            payload["staged_target"],
            max_copy_bytes=int(payload["max_copy_bytes"]),
        )
        sys.stdout.write(json.dumps({"copied": copied}))
        return 0
    except Exception as exc:
        sys.stdout.write(
            json.dumps({"error": str(exc), "error_type": type(exc).__name__})
        )
        return 0


async def apply_edits(path: str, edits: list[dict], dry_run: bool = False) -> str:
    """Apply text edits described by edits and return a formatted diff."""
    max_edit_bytes = FilesystemLimits.from_env().max_edit_bytes
    file_size = os.path.getsize(path)
    if file_size > max_edit_bytes:
        raise ValueError(
            f"File is too large for an atomic edit: {file_size} bytes; limit={max_edit_bytes}"
        )
    content = (await asyncio.to_thread(_read_file_sync, path)).replace("\r\n", "\n")
    modified = content
    
    for edit in edits:
        old_text = edit["oldText"].replace("\r\n", "\n")
        new_text = edit["newText"].replace("\r\n", "\n")
        
        if old_text in modified:
            modified = modified.replace(old_text, new_text, 1)
            continue
        
        # 行匹配
        old_lines = old_text.split("\n")
        content_lines = modified.split("\n")
        match_found = False
        
        for i in range(len(content_lines) - len(old_lines) + 1):
            potential = content_lines[i : i + len(old_lines)]
            if all(o.strip() == c.strip() for o, c in zip(old_lines, potential)):
                original_indent = content_lines[i][: len(content_lines[i]) - len(content_lines[i].lstrip())] if content_lines[i] else ""
                new_lines = new_text.split("\n")
                if new_lines:
                    new_lines[0] = original_indent + new_lines[0].lstrip()
                content_lines[i : i + len(old_lines)] = new_lines
                modified = "\n".join(content_lines)
                match_found = True
                break
        
        if not match_found:
            raise ValueError(f"Could not find match for edit:\n{edit['oldText']}")
    
    # 生成 diff
    diff_lines = unified_diff(
        content.splitlines(keepends=True),
        modified.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
        lineterm="",
    )
    diff_text = "".join(diff_lines)
    
    num_backticks = 3
    while "`" * num_backticks in diff_text:
        num_backticks += 1
    
    formatted_diff = f"{'`' * num_backticks}diff\n{diff_text}{'`' * num_backticks}\n\n"
    
    if not dry_run:
        await write_file(path, modified)
    
    return formatted_diff


async def edit_file_by_line_range(
    path: str,
    start_line: int,
    end_line: int,
    new_content: str,
    dry_run: bool = False,
) -> str:
    """Edit a file by 1-based line range and return a git-style diff. Does not write when dry_run=True."""
    from difflib import unified_diff

    if start_line <= 0 or end_line <= 0:
        raise ValueError("start_line and end_line must be positive (1-based)")
    if start_line > end_line:
        raise ValueError("start_line must be <= end_line")

    max_edit_bytes = FilesystemLimits.from_env().max_edit_bytes
    file_size = os.path.getsize(path)
    if file_size > max_edit_bytes:
        raise ValueError(
            f"File is too large for an atomic line edit: {file_size} bytes; limit={max_edit_bytes}"
        )
    original = await asyncio.to_thread(_read_file_sync, path)
    original_norm = original.replace("\r\n", "\n")
    lines = original_norm.split("\n")

    if not lines and new_content == "":
        return "No changes applied (empty file and empty new_content)."

    start_idx = start_line - 1
    end_idx = end_line
    if start_idx >= len(lines):
        raise ValueError(
            f"start_line {start_line} is beyond total line count {len(lines)}"
        )
    end_idx = min(end_idx, len(lines))

    new_norm = new_content.replace("\r\n", "\n")
    new_lines = [] if new_norm == "" else new_norm.split("\n")

    updated_lines = lines[:start_idx] + new_lines + lines[end_idx:]
    modified_norm = "\n".join(updated_lines)

    if original_norm.endswith("\n") and not modified_norm.endswith("\n"):
        modified_norm += "\n"

    diff_lines = unified_diff(
        original_norm.splitlines(keepends=True),
        modified_norm.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
        lineterm="",
    )
    diff_text = "".join(diff_lines)

    num_backticks = 3
    while "`" * num_backticks in diff_text:
        num_backticks += 1

    formatted_diff = f"{'`' * num_backticks}diff\n{diff_text}{'`' * num_backticks}\n\n"

    if not dry_run:
        await write_file(path, modified_norm)

    return formatted_diff


def _search_content_sync(
    path: str,
    pattern: str,
    max_matches: Optional[int],
    max_per_file: Optional[int],
    before: int,
    after: int,
) -> str:
    """Run content search in a killable, producer-bounded worker process."""

    return _run_search_worker(
        path,
        pattern=pattern,
        max_matches=max_matches,
        max_per_file=max_per_file,
        before=before,
        after=after,
        limits=FilesystemLimits.from_env(),
    ).text


def _limit_value(requested: Optional[int], hard_limit: int, name: str) -> int:
    if requested is None:
        return hard_limit
    if requested < 1:
        raise ValueError(f"{name} must be positive")
    return min(requested, hard_limit)


def _notice_limit(reason: Optional[str], limits: FilesystemLimits) -> int | float:
    return {
        "matches": limits.max_search_matches,
        "per_file_matches": limits.max_search_per_file,
        "search_files": limits.max_search_files,
        "search_bytes": limits.max_scan_bytes,
        "search_output_bytes": limits.max_search_output_bytes,
        "line_bytes": limits.max_line_bytes,
        "search_depth": limits.max_search_depth,
        "context_lines": limits.max_result_lines,
        "timeout": limits.search_timeout_seconds,
    }.get(reason or "", 0)


def _iter_regular_files(
    path: Path,
    *,
    limits: FilesystemLimits,
    state: dict,
):
    """Yield regular files without following directory symlinks."""

    if path.is_file():
        state["entries_scanned"] += 1
        yield path
        return

    stack: list[tuple[Path, int]] = [(path, 0)]
    visited: set[tuple[int, int]] = set()
    while stack and not state.get("stop"):
        current, depth = stack.pop()
        try:
            current_stat = current.stat()
        except OSError:
            continue
        inode = (current_stat.st_dev, current_stat.st_ino)
        if inode in visited:
            continue
        visited.add(inode)
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        with entries:
            for entry in entries:
                if state["entries_scanned"] >= limits.max_search_files:
                    state["reason"] = state.get("reason") or "search_files"
                    state["stop"] = True
                    break
                state["entries_scanned"] += 1
                try:
                    if entry.is_symlink():
                        # Never recurse through or inspect a link.  This both
                        # prevents cycles and keeps traversal inside its root.
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth >= limits.max_search_depth:
                            state["reason"] = state.get("reason") or "search_depth"
                            continue
                        stack.append((Path(entry.path), depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        yield Path(entry.path)
                except OSError:
                    continue


def _search_content_worker(payload: dict) -> BoundedSearchResult:
    limits = FilesystemLimits(**payload["limits"])
    pattern = payload["pattern"]
    try:
        re_obj = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern: {pattern}") from e

    before = payload["before"]
    after = payload["after"]
    if before < 0 or after < 0:
        raise ValueError("before and after must be non-negative")
    if before > limits.max_result_lines or after > limits.max_result_lines:
        before = min(before, limits.max_result_lines)
        after = min(after, limits.max_result_lines)
        initial_reason: Optional[str] = "context_lines"
    else:
        initial_reason = None

    effective_matches = _limit_value(
        payload.get("max_matches"), limits.max_search_matches, "max_matches"
    )
    effective_per_file = _limit_value(
        payload.get("max_per_file"), limits.max_search_per_file, "max_per_file"
    )
    results: list[str] = []
    total_matches = 0
    result_bytes = 0
    bytes_scanned = 0
    files_scanned = 0
    state: dict = {"entries_scanned": 0, "reason": initial_reason, "stop": False}

    def append_result(file_path: Path, line_no: int, content: str) -> bool:
        nonlocal result_bytes
        rendered = f"{file_path}:{line_no}:{content}"
        encoded_size = len(rendered.encode("utf-8")) + (1 if results else 0)
        if result_bytes + encoded_size > limits.max_search_output_bytes:
            state["reason"] = state.get("reason") or "search_output_bytes"
            state["stop"] = True
            return False
        results.append(rendered)
        result_bytes += encoded_size
        return True

    def process_file(file_path: Path) -> None:
        nonlocal total_matches, bytes_scanned, files_scanned
        files_scanned += 1
        per_file_matches = 0
        previous: deque[tuple[int, str]] = deque(maxlen=before)
        last_emitted = 0
        after_remaining = 0
        global_limit_reached = False
        line_no = 0

        try:
            stream = open(file_path, "rb")
        except OSError:
            return
        with stream:
            while not state.get("stop"):
                if bytes_scanned >= limits.max_scan_bytes:
                    state["reason"] = state.get("reason") or "search_bytes"
                    state["stop"] = True
                    break
                raw, scanned, line_truncated, scan_exhausted = _read_physical_line(
                    stream,
                    max_line_bytes=limits.max_line_bytes,
                    remaining_scan_bytes=limits.max_scan_bytes - bytes_scanned,
                )
                bytes_scanned += scanned
                if scanned == 0:
                    break
                line_no += 1
                try:
                    content, _ = _decode_utf8_prefix(raw, complete=not line_truncated)
                except UnicodeDecodeError:
                    # Preserve the previous behavior of ignoring non-UTF-8 files.
                    return
                content = content.rstrip("\n\r")
                if line_truncated:
                    state["reason"] = state.get("reason") or "line_bytes"

                matched = re_obj.search(content) is not None
                if (
                    matched
                    and not global_limit_reached
                    and per_file_matches < effective_per_file
                ):
                    for context_line, context_content in previous:
                        if context_line > last_emitted:
                            if not append_result(file_path, context_line, context_content):
                                return
                            last_emitted = context_line
                    if line_no > last_emitted:
                        if not append_result(file_path, line_no, content):
                            return
                        last_emitted = line_no
                    total_matches += 1
                    per_file_matches += 1
                    after_remaining = after
                    if total_matches >= effective_matches:
                        state["reason"] = state.get("reason") or "matches"
                        global_limit_reached = True
                        if after_remaining <= 0:
                            state["stop"] = True
                elif after_remaining > 0 and line_no > last_emitted:
                    if not append_result(file_path, line_no, content):
                        return
                    last_emitted = line_no
                    after_remaining -= 1

                previous.append((line_no, content))
                if scan_exhausted:
                    state["reason"] = state.get("reason") or "search_bytes"
                    state["stop"] = True
                if global_limit_reached and after_remaining <= 0:
                    state["stop"] = True
                if per_file_matches >= effective_per_file and after_remaining <= 0:
                    # There may be more matches in this file; make the cap visible.
                    state["reason"] = state.get("reason") or "per_file_matches"
                    break

    path_obj = Path(payload["path"]).resolve()
    for candidate in _iter_regular_files(path_obj, limits=limits, state=state):
        if state.get("stop"):
            break
        process_file(candidate)

    reason = state.get("reason")
    text = "\n".join(results) if results else (
        "No matches found" if reason is None else "Search incomplete; no matches observed before truncation"
    )
    if reason:
        notice_limit: int | float
        if reason == "matches":
            notice_limit = effective_matches
        elif reason == "per_file_matches":
            notice_limit = effective_per_file
        else:
            notice_limit = _notice_limit(reason, limits)
        text += "\n" + truncation_notice(reason=reason, limit=notice_limit)
    return BoundedSearchResult(
        text=text,
        complete=reason is None,
        matches=total_matches,
        files_scanned=files_scanned,
        bytes_scanned=bytes_scanned,
        truncation_reason=reason,
    )


def _terminate_worker(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _run_search_worker(
    path: str,
    *,
    pattern: str,
    max_matches: Optional[int],
    max_per_file: Optional[int],
    before: int,
    after: int,
    limits: FilesystemLimits,
) -> BoundedSearchResult:
    # Compile once in the parent so syntax errors remain immediate and compatible.
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {pattern}") from exc
    if before < 0 or after < 0:
        raise ValueError("before and after must be non-negative")
    _limit_value(max_matches, limits.max_search_matches, "max_matches")
    _limit_value(max_per_file, limits.max_search_per_file, "max_per_file")

    payload = {
        "path": path,
        "pattern": pattern,
        "max_matches": max_matches,
        "max_per_file": max_per_file,
        "before": before,
        "after": after,
        "limits": asdict(limits),
    }
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--search-worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=limits.search_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        _terminate_worker(process)
        process.communicate()
        reason = "timeout"
        return BoundedSearchResult(
            text="Search incomplete; no matches observed before truncation\n"
            + truncation_notice(reason=reason, limit=limits.search_timeout_seconds),
            complete=False,
            matches=0,
            files_scanned=0,
            bytes_scanned=0,
            truncation_reason=reason,
            timed_out=True,
        )
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "filesystem search worker failed")
    try:
        decoded = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("filesystem search worker returned invalid output") from exc
    if "error" in decoded:
        raise ValueError(decoded["error"])
    return BoundedSearchResult(**decoded)


async def search_content(
    path: str,
    pattern: str,
    max_matches: Optional[int] = None,
    max_per_file: Optional[int] = None,
    before: int = 0,
    after: int = 0,
) -> str:
    """Search UTF-8 files using a killable, bounded worker process."""
    return await asyncio.to_thread(
        _search_content_sync, path, pattern, max_matches, max_per_file, before, after
    )


def search_files_bounded(
    path: str,
    *,
    pattern: str,
    exclude_patterns: list[str],
    limits: Optional[FilesystemLimits] = None,
) -> BoundedTraversalResult:
    """Recursively match paths without following symlinks or growing unbounded."""

    limits = limits or FilesystemLimits.from_env()
    root = Path(path).resolve()
    state: dict = {"entries_scanned": 0, "reason": None, "stop": False}
    paths: list[str] = []
    output_bytes = 0
    deadline = time.monotonic() + limits.search_timeout_seconds
    stack: list[tuple[Path, int]] = [(root, 0)]
    visited: set[tuple[int, int]] = set()

    def excluded(relative: str) -> bool:
        return any(
            fnmatch(relative, item) or fnmatch(relative, f"**/{item}")
            for item in exclude_patterns
        )

    while stack and not state["stop"]:
        if time.monotonic() >= deadline:
            state["reason"] = "timeout"
            break
        current, depth = stack.pop()
        try:
            current_stat = current.stat()
        except OSError:
            continue
        inode = (current_stat.st_dev, current_stat.st_ino)
        if inode in visited:
            continue
        visited.add(inode)
        try:
            iterator = os.scandir(current)
        except OSError:
            continue
        with iterator:
            for entry in iterator:
                if time.monotonic() >= deadline:
                    state["reason"] = "timeout"
                    state["stop"] = True
                    break
                if state["entries_scanned"] >= limits.max_search_files:
                    state["reason"] = "search_files"
                    state["stop"] = True
                    break
                state["entries_scanned"] += 1
                try:
                    if entry.is_symlink():
                        continue
                    full_path = Path(entry.path)
                    relative = str(full_path.relative_to(root))
                    if excluded(relative):
                        continue
                    is_directory = entry.is_dir(follow_symlinks=False)
                    if fnmatch(relative, pattern) or fnmatch(entry.name, pattern):
                        rendered = str(full_path)
                        encoded_size = len(rendered.encode("utf-8")) + (1 if paths else 0)
                        if output_bytes + encoded_size > limits.max_search_output_bytes:
                            state["reason"] = "search_output_bytes"
                            state["stop"] = True
                            break
                        paths.append(rendered)
                        output_bytes += encoded_size
                    if is_directory:
                        if depth >= limits.max_search_depth:
                            state["reason"] = state.get("reason") or "search_depth"
                        else:
                            stack.append((full_path, depth + 1))
                except OSError:
                    continue

    return BoundedTraversalResult(
        paths=tuple(paths),
        complete=state.get("reason") is None,
        entries_scanned=state["entries_scanned"],
        truncation_reason=state.get("reason"),
    )


async def read_media_file(path: str) -> tuple[str, str, str]:
    """Read a media file and return (base64, MIME type, media type)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_media_file_sync, path)


def _read_media_file_sync(path: str) -> tuple[str, str, str]:
    """Synchronously read a bounded media chunk for legacy helper callers."""
    mime_type, _ = mimetypes.guess_type(path)
    if not mime_type:
        mime_type = "application/octet-stream"
    
    limits = FilesystemLimits.from_env()
    chunk = _read_binary_chunk_sync(path, 0, None, limits.max_binary_bytes)
    data = base64.b64encode(chunk.data).decode("utf-8")
    
    if mime_type.startswith("image/"):
        media_type = "image"
    elif mime_type.startswith("audio/"):
        media_type = "audio"
    else:
        media_type = "blob"
    
    return data, mime_type, media_type


async def get_file_stats(path: str) -> dict:
    """Get basic file stat information asynchronously."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_file_stats_sync, path)


def _get_file_stats_sync(path: str) -> dict:
    """Synchronously get basic file stat information."""
    stat = Path(path).stat()
    return {
        "size": stat.st_size,
        "created": stat.st_ctime,
        "modified": stat.st_mtime,
        "accessed": stat.st_atime,
        "isDirectory": Path(path).is_dir(),
        "isFile": Path(path).is_file(),
        "permissions": oct(stat.st_mode)[-3:],
    }


def _search_worker_main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        result = _search_content_worker(payload)
        sys.stdout.write(json.dumps(asdict(result), ensure_ascii=False))
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    if "--copy-worker" in sys.argv:
        raise SystemExit(_copy_worker_main())
    if "--search-worker" in sys.argv:
        raise SystemExit(_search_worker_main())
