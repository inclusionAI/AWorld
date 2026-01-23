# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""File operation functions."""

import asyncio
from pathlib import Path
import tempfile
from difflib import unified_diff


async def read_file(path: str) -> str:
    """Read file content."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_file_sync, path)


def _read_file_sync(path: str) -> str:
    """Synchronous read file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


async def write_file(path: str, content: str) -> None:
    """Atomically write file."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write_file_sync, path, content)


def _write_file_sync(path: str, content: str) -> None:
    """Synchronous atomic write file."""
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
    """Read first N lines."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _head_file_sync, path, num_lines)


def _head_file_sync(path: str, num_lines: int) -> str:
    """Synchronous read first N lines."""
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= num_lines:
                break
            lines.append(line.rstrip("\n\r"))
    return "\n".join(lines)


async def tail_file(path: str, num_lines: int) -> str:
    """Read last N lines."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _tail_file_sync, path, num_lines)


def _tail_file_sync(path: str, num_lines: int) -> str:
    """Synchronous read last N lines."""
    with open(path, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-num_lines:])


async def apply_edits(path: str, edits: list[dict], dry_run: bool = False) -> str:
    """Apply edits and return diff."""
    content = (await read_file(path)).replace("\r\n", "\n")
    modified = content
    
    for edit in edits:
        old_text = edit["oldText"].replace("\r\n", "\n")
        new_text = edit["newText"].replace("\r\n", "\n")
        
        if old_text in modified:
            modified = modified.replace(old_text, new_text, 1)
            continue
        
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

