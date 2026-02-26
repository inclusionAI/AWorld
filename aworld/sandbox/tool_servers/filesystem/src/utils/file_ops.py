"""File operation helper functions."""

import asyncio
from pathlib import Path
import tempfile
import base64
import mimetypes
from difflib import unified_diff

# Number of bytes to sample when detecting whether a file is text
TEXT_SAMPLE_SIZE = 8192


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
    """Read file content as UTF-8 text."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_file_sync, path)


def _read_file_sync(path: str) -> str:
    """Synchronously read file content as UTF-8 text."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


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


async def head_file(path: str, num_lines: int) -> str:
    """Read the first N lines of a text file."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _head_file_sync, path, num_lines)


def _head_file_sync(path: str, num_lines: int) -> str:
    """Synchronously read the first N lines of a text file."""
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= num_lines:
                break
            lines.append(line.rstrip("\n\r"))
    return "\n".join(lines)


async def tail_file(path: str, num_lines: int) -> str:
    """Read the last N lines of a text file."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _tail_file_sync, path, num_lines)


def _tail_file_sync(path: str, num_lines: int) -> str:
    """Synchronously read the last N lines of a text file."""
    with open(path, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-num_lines:])


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
    """Synchronously read lines [start, end] (1-based inclusive) from a text file."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    # 1-based -> 0-based slice [start-1:end]
    start_idx = max(0, start_1based - 1)
    end_idx = min(len(lines), end_1based)
    if start_idx >= end_idx:
        return ""
    selected = lines[start_idx:end_idx]
    return "".join(selected).rstrip("\n\r")


async def read_file_lines(path: str, start_1based: int, end_1based: int) -> str:
    """Read lines [start, end] (1-based inclusive) from a text file."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _read_file_lines_sync, path, start_1based, end_1based
    )


def _read_file_binary_sync(path: str) -> bytes:
    """Synchronously read a file as raw bytes."""
    with open(path, "rb") as f:
        return f.read()


async def read_file_binary(path: str) -> bytes:
    """Read a file as raw bytes."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_file_binary_sync, path)


def get_mime_and_filename(path: str) -> tuple[str, str]:
    """Return (mime_type, filename) for a given path."""
    mime_type, _ = mimetypes.guess_type(path)
    if not mime_type:
        mime_type = "application/octet-stream"
    filename = Path(path).name
    return mime_type, filename


def _apply_edits_range_sync(path: str, start: int, end: int, new_content: str) -> None:
    """Synchronously replace content[start:end] with new_content; start==end inserts; empty new_content deletes."""
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


def _copy_file_binary_sync(source: str, target: str) -> None:
    """Synchronously copy binary file source -> target, overwriting target and creating parent directories."""
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    with open(source, "rb") as f:
        data = f.read()
    with open(target, "wb") as f:
        f.write(data)


async def copy_file_binary(source: str, target: str) -> None:
    """Asynchronously copy binary file source -> target."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _copy_file_binary_sync, source, target)


async def apply_edits(path: str, edits: list[dict], dry_run: bool = False) -> str:
    """Apply text edits described by edits and return a formatted diff."""
    content = (await read_file(path)).replace("\r\n", "\n")
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

    original = await read_file(path)
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


async def read_media_file(path: str) -> tuple[str, str, str]:
    """Read a media file and return (base64, MIME type, media type)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_media_file_sync, path)


def _read_media_file_sync(path: str) -> tuple[str, str, str]:
    """Synchronously read a media file and return (base64, MIME type, media type)."""
    mime_type, _ = mimetypes.guess_type(path)
    if not mime_type:
        mime_type = "application/octet-stream"
    
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    
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

