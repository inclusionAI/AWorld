"""

Provides high-performance text search and file discovery based on grep.
Includes RipgrepSearcher (preferred, when rg available) and PygrepSearcher (fallback).
"""

import asyncio
import fnmatch
import json
import os
import re
import shutil
import signal
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..utils import logger
from .path_policy import canonical_root, resolve_within_root


DEFAULT_SEARCH_TIMEOUT_SECONDS = 10.0
MAX_SEARCH_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_SEARCH_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_CAPTURE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_LINE_LENGTH = 2000
DEFAULT_MAX_FILE_RESULTS = 1000
_READ_CHUNK_BYTES = 64 * 1024


class SearchTimeoutError(TimeoutError):
    """Raised when a search producer exceeds its deadline."""


class BoundedResults(list):
    """List-compatible results carrying producer completeness metadata."""

    def __init__(self, values=(), *, truncated: bool = False, reason: Optional[str] = None):
        super().__init__(values)
        self.truncated = truncated
        self.truncation_reason = reason


def _read_bounded_binary_line(handle, *, content_limit: int, scan_limit: int):
    """Read one logical line without ever materializing an unbounded line."""
    if scan_limit <= 0:
        return None
    first = handle.readline(min(content_limit + 1, scan_limit))
    if not first:
        return None

    consumed = len(first)
    ended_with_newline = first.endswith(b"\n")
    complete = ended_with_newline
    preview = first
    while not complete and consumed < scan_limit:
        chunk = handle.readline(min(_READ_CHUNK_BYTES, scan_limit - consumed))
        if not chunk:
            complete = True
            break
        consumed += len(chunk)
        ended_with_newline = chunk.endswith(b"\n")
        complete = ended_with_newline

    stripped = preview.rstrip(b"\n\r")
    line_truncated = len(stripped) > content_limit or not complete
    stripped = stripped[:content_limit]
    text = stripped.decode("utf-8", errors="replace")
    if line_truncated:
        text += "..."
    return text, consumed, complete, line_truncated, ended_with_newline


@dataclass
class GrepMatch:
    """grep search match result"""
    file_path: str
    line_number: int
    line_text: str
    absolute_offset: int
    submatches: List[Dict[str, Any]]
    mod_time: float = 0.0


@dataclass
class GrepStats:
    """grep search statistics"""
    elapsed_secs: float
    searches: int
    searches_with_match: int
    bytes_searched: int
    bytes_printed: int
    matched_lines: int
    matches: int

# Default directories to exclude from search (reduces I/O on large codebases)
_DEFAULT_EXCLUDE_DIRS = frozenset({
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env',
    'dist', 'build', '.eggs', '.tox', '.mypy_cache', '.ruff_cache',
    '.pytest_cache', '.hypothesis', 'vendor', '.svn',
})

class PygrepSearcher:
    """
    Grep‑like searcher implemented in pure Python.

    Uses Python's ``re`` module and filesystem traversal to implement text search
    as a fallback when ripgrep is not available.
    Provides the same interface as ``grepSearcher`` and can be used as a drop‑in replacement.
    """

    def __init__(self):
        """Initialize the Pygrep searcher"""
        pass

    async def ensure_installed(self):
        """Ensure the searcher is available (Python implementation needs no installation)"""
        pass

    def _should_exclude_dir(self, dir_name: str) -> bool:
        """Check whether a directory should be excluded from traversal."""
        return dir_name in _DEFAULT_EXCLUDE_DIRS

    def _should_include_file(self, file_path: Path, include_patterns: Optional[List[str]] = None) -> bool:
        """Check whether a file should be included in the search"""
        # Exclude .git directory
        if '.git' in file_path.parts:
            return False
        
        # If no include patterns are specified, include all files
        if not include_patterns:
            return True
        
        # Check whether the file matches any include pattern
        file_str = str(file_path)
        for pattern in include_patterns:
            # Support glob‑style matching
            if fnmatch.fnmatch(file_str, pattern) or fnmatch.fnmatch(file_path.name, pattern):
                return True
        
        return False

    def _is_binary_file(self, file_path: Path) -> bool:
        """Detect whether the file is binary"""
        try:
            # First check by file extension
            binary_extensions = {
                '.zip', '.tar', '.gz', '.exe', '.dll', '.so', '.class', '.jar',
                '.war', '.7z', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                '.odt', '.ods', '.odp', '.bin', '.dat', '.obj', '.o', '.a',
                '.lib', '.wasm', '.pyc', '.pyo', '.png', '.jpg', '.jpeg', '.gif',
                '.bmp', '.ico', '.svg', '.pdf', '.mp3', '.mp4', '.avi', '.mov'
            }
            if file_path.suffix.lower() in binary_extensions:
                return True
            
            # Then check by sampling file content
            try:
                with open(file_path, 'rb') as f:
                    chunk = f.read(4096)
                    if b'\x00' in chunk:
                        return True
                    # Check ratio of non‑printable characters
                    non_printable = sum(1 for byte in chunk if byte < 9 or (byte > 13 and byte < 32))
                    if len(chunk) > 0 and (non_printable / len(chunk)) > 0.3:
                        return True
            except Exception:
                return True
        except Exception:
            return True
        
        return False

    async def search(self,
                    pattern: str,
                    path: str = ".",
                    include_patterns: Optional[List[str]] = None,
                    max_count: Optional[int] = None,
                    context_lines: int = 0,
                    case_sensitive: bool = False,
                    follow_symlinks: bool = True,
                    search_hidden: bool = True,
                    timeout_seconds: Optional[float] = None,
                    max_scan_bytes: Optional[int] = None,
                    max_line_length: int = DEFAULT_MAX_LINE_LENGTH) -> List[GrepMatch]:
        """Run the Python regex fallback in a killable subprocess."""

        timeout = min(
            MAX_SEARCH_TIMEOUT_SECONDS,
            max(0.01, float(timeout_seconds or DEFAULT_SEARCH_TIMEOUT_SECONDS)),
        )
        payload = {
            "pattern": pattern,
            "path": path,
            "include_patterns": include_patterns,
            "max_count": min(
                max(1, int(max_count or DEFAULT_MAX_FILE_RESULTS)),
                DEFAULT_MAX_FILE_RESULTS,
            ),
            "context_lines": context_lines,
            "case_sensitive": case_sensitive,
            "follow_symlinks": follow_symlinks,
            "search_hidden": search_hidden,
            "timeout_seconds": timeout,
            "max_scan_bytes": max_scan_bytes,
            "max_line_length": max_line_length,
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "aworld.experimental.cast.searchers.utils",
            "--pygrep-worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(payload).encode("utf-8")),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            if process.returncode is None:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
            await process.communicate()
            raise SearchTimeoutError(
                f"Python grep search timed out after {timeout:.2f}s"
            ) from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "Python grep worker failed")
        try:
            decoded = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Python grep worker returned invalid output") from exc
        if "error" in decoded:
            raise ValueError(decoded["error"])
        return BoundedResults(
            (GrepMatch(**item) for item in decoded.get("matches", [])),
            truncated=bool(decoded.get("truncated")),
            reason=decoded.get("truncation_reason"),
        )

    async def _search_inline(self,
                    pattern: str,
                    path: str = ".",
                    include_patterns: Optional[List[str]] = None,
                    max_count: Optional[int] = None,
                    context_lines: int = 0,
                    case_sensitive: bool = False,
                    follow_symlinks: bool = True,
                    search_hidden: bool = True,
                    timeout_seconds: Optional[float] = None,
                    max_scan_bytes: Optional[int] = None,
                    max_line_length: int = DEFAULT_MAX_LINE_LENGTH) -> List[GrepMatch]:
        """
        Execute a content search.

        Args:
            pattern: Search pattern (regular expression).
            path: Search path.
            include_patterns: List of file glob patterns to include.
            max_count: Maximum number of matches.
            context_lines: Number of context lines (currently unused).
            case_sensitive: Whether the search is case‑sensitive.
            follow_symlinks: Whether to follow symbolic links.
            search_hidden: Whether to search hidden files.

        Returns:
            List of match results.
        """
        search_path = canonical_root(path) if Path(path).is_dir() else Path(path).resolve(strict=True)

        # Compile regular expression
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regular expression pattern: {pattern}, error: {e}")
        if re.search(r"\([^)]*[+*][^)]*\)\s*(?:[+*]|\{)", pattern):
            raise ValueError("Potentially exponential nested repetition is not supported")

        matches = BoundedResults()
        match_count = 0
        files_scanned = [0]  # mutable for nested function
        bytes_scanned = [0]
        scan_budget = min(
            DEFAULT_MAX_SEARCH_BYTES,
            max(1, int(max_scan_bytes or DEFAULT_MAX_SEARCH_BYTES)),
        )
        timeout = min(
            MAX_SEARCH_TIMEOUT_SECONDS,
            max(0.01, float(timeout_seconds or DEFAULT_SEARCH_TIMEOUT_SECONDS)),
        )
        deadline = time.monotonic() + timeout
        root = search_path if search_path.is_dir() else search_path.parent
        visited_dirs = set()
        visited_files = set()

        def budget_exhausted() -> bool:
            if time.monotonic() >= deadline:
                matches.truncated = True
                matches.truncation_reason = "timeout"
                return True
            if bytes_scanned[0] >= scan_budget:
                matches.truncated = True
                matches.truncation_reason = "byte_budget"
                return True
            return False

        # Traverse files
        def search_files():
            nonlocal match_count
            walk_target = search_path if search_path.is_dir() else search_path.parent
            for current_root, dirs, files in os.walk(walk_target, followlinks=follow_symlinks):
                if budget_exhausted():
                    return
                try:
                    current = resolve_within_root(root, current_root)
                    stat = current.stat()
                    inode = (stat.st_dev, stat.st_ino)
                    if inode in visited_dirs:
                        dirs[:] = []
                        continue
                    visited_dirs.add(inode)
                except (OSError, PermissionError, ValueError):
                    dirs[:] = []
                    continue

                # Filter directories: hidden, and default exclude list
                safe_dirs = []
                for directory in dirs:
                    if (not search_hidden and directory.startswith('.')) or self._should_exclude_dir(directory):
                        continue
                    candidate = Path(current_root) / directory
                    if candidate.is_symlink() and not follow_symlinks:
                        continue
                    try:
                        resolved_dir = resolve_within_root(root, candidate)
                        child_stat = resolved_dir.stat()
                        if (child_stat.st_dev, child_stat.st_ino) in visited_dirs:
                            continue
                    except (OSError, PermissionError, ValueError):
                        continue
                    safe_dirs.append(directory)
                dirs[:] = safe_dirs

                for file_name in files:
                    if budget_exhausted():
                        return
                    # Skip hidden files if required
                    if not search_hidden and file_name.startswith('.'):
                        continue
                    
                    file_path = Path(current_root) / file_name
                    if search_path.is_file() and file_path.resolve() != search_path:
                        continue
                    if file_path.is_symlink() and not follow_symlinks:
                        continue
                    try:
                        file_path = resolve_within_root(root, file_path)
                        file_stat = file_path.stat()
                        file_inode = (file_stat.st_dev, file_stat.st_ino)
                        if file_inode in visited_files:
                            continue
                        visited_files.add(file_inode)
                    except (OSError, PermissionError, ValueError):
                        continue
                    
                    # Check whether this file should be included
                    if not self._should_include_file(file_path, include_patterns):
                        continue
                    
                    # Skip binary files
                    if self._is_binary_file(file_path):
                        continue
                    
                    # Stop when reaching the maximum number of matches
                    if max_count and match_count >= max_count:
                        return

                    # Get file modification time once per file (avoids repeated stat syscalls)
                    try:
                        mod_time = os.path.getmtime(file_path)
                    except OSError:
                        mod_time = 0.0

                    files_scanned[0] += 1
                    if files_scanned[0] % 50 == 0:
                        logger.debug(f"Pygrep scan progress: {files_scanned[0]} files scanned")
                    try:
                        # Read bounded binary chunks so a single huge line cannot
                        # allocate an equally huge Python string.
                        with open(file_path, 'rb') as f:
                            absolute_offset = 0
                            line_num = 0
                            while not budget_exhausted():
                                item = _read_bounded_binary_line(
                                    f,
                                    content_limit=max(1, max_line_length),
                                    scan_limit=scan_budget - bytes_scanned[0],
                                )
                                if item is None:
                                    break
                                line_num += 1
                                line_text, consumed, complete, line_was_truncated, _ = item
                                bytes_scanned[0] += consumed
                                search_text = (
                                    line_text[:-3]
                                    if line_was_truncated and line_text.endswith("...")
                                    else line_text
                                )
                                if line_was_truncated:
                                    matches.truncated = True
                                    matches.truncation_reason = (
                                        matches.truncation_reason or "line_budget"
                                    )

                                for match in regex.finditer(search_text):
                                    submatches = []
                                    for i, group in enumerate(match.groups(), start=1):
                                        if group is not None:
                                            submatches.append({
                                                'start': match.start(i),
                                                'end': match.end(i),
                                                'match': {'text': group}
                                            })
                                    submatches.insert(0, {
                                        'start': match.start(),
                                        'end': match.end(),
                                        'match': {'text': match.group()}
                                    })

                                    match_obj = GrepMatch(
                                        file_path=str(file_path),
                                        line_number=line_num,
                                        line_text=line_text,
                                        absolute_offset=absolute_offset + match.start(),
                                        submatches=submatches,
                                        mod_time=mod_time
                                    )
                                    matches.append(match_obj)
                                    match_count += 1
                                    if max_count and match_count >= max_count:
                                        matches.truncated = True
                                        matches.truncation_reason = "result_limit"
                                        break

                                absolute_offset += consumed
                                if not complete:
                                    matches.truncated = True
                                    matches.truncation_reason = "byte_budget"
                                if (max_count and match_count >= max_count) or not complete:
                                    break

                    except (UnicodeDecodeError, PermissionError, OSError) as e:
                        logger.debug(f"Skip file {file_path}: {e}")
                        continue

        # Run the search in a thread pool
        start_time = time.perf_counter()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, search_files)
        elapsed = time.perf_counter() - start_time

        # Sort by modification time
        matches.sort(key=lambda m: m.mod_time, reverse=True)

        n = files_scanned[0]
        b = bytes_scanned[0]
        size_str = f"{b / 1024:.1f}KB" if b < 1024 * 1024 else f"{b / 1024 / 1024:.1f}MB"
        logger.info(
            f"Pygrep scanned {n} files, {size_str} text in {elapsed:.2f}s "
            f"(pattern='{pattern[:50]}{'...' if len(pattern) > 50 else ''}', {len(matches)} matches)"
        )
        logger.debug(f"Pygrep search finished: pattern='{pattern}', found {len(matches)} matches")
        return matches

    async def find_files(self,
                        path: str = ".",
                        include_patterns: Optional[List[str]] = None,
                        max_depth: Optional[int] = None,
                        follow_symlinks: bool = True,
                        search_hidden: bool = True,
                        max_count: Optional[int] = None,
                        timeout_seconds: Optional[float] = None) -> List[str]:
        """
        Discover files.

        Args:
            path: Root search path.
            include_patterns: File glob patterns to include.
            max_depth: Maximum directory traversal depth.
            follow_symlinks: Whether to follow symbolic links.
            search_hidden: Whether to include hidden files.

        Returns:
            List of file paths.
        """
        search_path = canonical_root(path)

        file_paths = BoundedResults()
        result_limit = max(1, int(max_count or DEFAULT_MAX_FILE_RESULTS))
        deadline = time.monotonic() + min(
            MAX_SEARCH_TIMEOUT_SECONDS,
            max(0.01, float(timeout_seconds or DEFAULT_SEARCH_TIMEOUT_SECONDS)),
        )
        visited_dirs = set()
        visited_files = set()

        def find_files_recursive(current_path: Path, current_depth: int = 0):
            if len(file_paths) >= result_limit:
                file_paths.truncated = True
                file_paths.truncation_reason = "result_limit"
                return
            if time.monotonic() >= deadline:
                file_paths.truncated = True
                file_paths.truncation_reason = "timeout"
                return
            # Check depth limit
            if max_depth is not None and current_depth > max_depth:
                return
            
            try:
                current_path = resolve_within_root(search_path, current_path)
                stat = current_path.stat()
                inode = (stat.st_dev, stat.st_ino)
                if inode in visited_dirs:
                    return
                visited_dirs.add(inode)
                # Walk current directory
                for item in current_path.iterdir():
                    if len(file_paths) >= result_limit or time.monotonic() >= deadline:
                        file_paths.truncated = True
                        file_paths.truncation_reason = (
                            "result_limit" if len(file_paths) >= result_limit else "timeout"
                        )
                        return
                    # Skip hidden files/directories if required
                    if not search_hidden and item.name.startswith('.'):
                        continue
                    
                    # Exclude .git directory
                    if item.name == '.git' and item.is_dir():
                        continue
                    
                    # Handle symbolic links
                    if item.is_symlink():
                        if not follow_symlinks:
                            continue
                        try:
                            item = resolve_within_root(search_path, item)
                        except (OSError, RuntimeError, PermissionError, ValueError):
                            continue
                    else:
                        try:
                            item = resolve_within_root(search_path, item)
                        except (OSError, PermissionError, ValueError):
                            continue
                    
                    if item.is_file():
                        # Check whether it matches the include patterns before
                        # inode de-duplication so an excluded hardlink cannot
                        # hide an included path.
                        if not self._should_include_file(item, include_patterns):
                            continue
                        try:
                            item_stat = item.stat()
                            item_inode = (item_stat.st_dev, item_stat.st_ino)
                            if item_inode in visited_files:
                                continue
                            visited_files.add(item_inode)
                        except OSError:
                            continue
                        file_paths.append(str(item))
                    elif item.is_dir():
                        find_files_recursive(item, current_depth + 1)
            
            except (PermissionError, OSError) as e:
                logger.debug(f"Cannot access directory {current_path}: {e}")

        # Run file discovery in a thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, find_files_recursive, search_path, 0)

        logger.debug(f"Pygrep file discovery finished: found {len(file_paths)} files")
        return file_paths


def _ripgrep_available() -> bool:
    """Check if ripgrep (rg) is installed and available."""
    return shutil.which("rg") is not None


async def grep_search_with_fallback(
    pattern: str,
    path: str = ".",
    include_patterns: Optional[List[str]] = None,
    max_count: Optional[int] = None,
    context_lines: int = 0,
    case_sensitive: bool = False,
    follow_symlinks: bool = True,
    search_hidden: bool = True,
    timeout_seconds: Optional[float] = None,
    max_scan_bytes: Optional[int] = None,
    max_output_bytes: Optional[int] = None,
    max_line_length: int = DEFAULT_MAX_LINE_LENGTH,
) -> List[GrepMatch]:
    """
    Unified grep search: use ripgrep when available, fall back to Pygrep on any failure.

    - If ripgrep is installed and runs successfully → use its results.
    - If ripgrep is not installed, or ripgrep errors → automatically fall back to Pygrep.
    """
    search_kw = dict(
        pattern=pattern,
        path=path,
        include_patterns=include_patterns,
        max_count=max_count,
        context_lines=context_lines,
        case_sensitive=case_sensitive,
        follow_symlinks=follow_symlinks,
        search_hidden=search_hidden,
        timeout_seconds=timeout_seconds,
        max_scan_bytes=max_scan_bytes,
        max_output_bytes=max_output_bytes,
        max_line_length=max_line_length,
    )
    rg = RipgrepSearcher()
    py = PygrepSearcher()
    try:
        return await rg.search(**search_kw)
    except SearchTimeoutError:
        raise
    except Exception as e:
        logger.info(f"Ripgrep unavailable or failed, using Pygrep: {e}")
        search_kw.pop("max_output_bytes", None)
        return await py.search(**search_kw)


class RipgrepSearcher:
    """
    High-performance searcher using ripgrep (rg) when available.

    Uses subprocess to invoke rg with JSON output, typically 10-100x faster
    than PygrepSearcher on large codebases.
    Raises on failure so caller can fall back to PygrepSearcher.
    """

    def __init__(self):
        self._available: Optional[bool] = None

    def _check_available(self) -> bool:
        if self._available is None:
            self._available = _ripgrep_available()
            if self._available:
                logger.debug("RipgrepSearcher: rg available, using ripgrep for search")
            else:
                logger.debug("RipgrepSearcher: rg not found, fallback to PygrepSearcher")
        return self._available

    def is_available(self) -> bool:
        """Return True if ripgrep (rg) is installed and usable."""
        return self._check_available()

    async def ensure_installed(self) -> bool:
        """Check if ripgrep is available."""
        return self._check_available()

    async def search(self,
                    pattern: str,
                    path: str = ".",
                    include_patterns: Optional[List[str]] = None,
                    max_count: Optional[int] = None,
                    context_lines: int = 0,
                    case_sensitive: bool = False,
                    follow_symlinks: bool = True,
                    search_hidden: bool = True,
                    timeout_seconds: Optional[float] = None,
                    max_scan_bytes: Optional[int] = None,
                    max_output_bytes: Optional[int] = None,
                    max_line_length: int = DEFAULT_MAX_LINE_LENGTH) -> List[GrepMatch]:
        """
        Execute content search via ripgrep.

        Returns:
            List of GrepMatch on success.

        Raises:
            RuntimeError: When ripgrep is not installed or fails (caller should fall back to Pygrep).
        """
        if not self._check_available():
            raise RuntimeError("ripgrep (rg) is not installed or not in PATH")

        search_path = Path(path)
        if not search_path.exists():
            raise ValueError(f"Search path does not exist: {path}")

        path_str = str(search_path.resolve())
        authority_root = search_path.resolve() if search_path.is_dir() else search_path.resolve().parent
        line_limit = max(1, int(max_line_length))
        capture_budget = min(
            DEFAULT_MAX_CAPTURE_BYTES,
            max(1024, int(max_output_bytes or DEFAULT_MAX_CAPTURE_BYTES)),
        )
        scan_budget = min(
            DEFAULT_MAX_SEARCH_BYTES,
            max(1024, int(max_scan_bytes or DEFAULT_MAX_SEARCH_BYTES)),
        )
        timeout = min(
            MAX_SEARCH_TIMEOUT_SECONDS,
            max(0.01, float(timeout_seconds or DEFAULT_SEARCH_TIMEOUT_SECONDS)),
        )
        cmd = [
            "rg", "--json", "-n", "--no-heading", "--no-column",
            f"--max-columns={line_limit}", "--max-columns-preview",
            f"--max-filesize={scan_budget}",
        ]
        if not case_sensitive:
            cmd.append("-i")
        if context_lines > 0:
            cmd.append(f"-C{context_lines}")
        if not follow_symlinks:
            cmd.append("--no-follow")
        if search_hidden:
            cmd.append("--hidden")
        if include_patterns:
            for g in include_patterns:
                cmd.extend(["-g", g])
        cmd.extend(["-e", pattern, path_str])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=(os.name == "posix"),
            )
            matches = BoundedResults()
            mod_time_cache: Dict[str, float] = {}

            async def consume_stderr() -> bytes:
                kept = bytearray()
                while True:
                    chunk = await proc.stderr.read(8 * 1024)
                    if not chunk:
                        break
                    remaining = 16 * 1024 - len(kept)
                    if remaining > 0:
                        kept.extend(chunk[:remaining])
                return bytes(kept)

            stderr_task = asyncio.create_task(consume_stderr())

            async def consume_stdout() -> None:
                captured = 0
                while True:
                    raw_line = await proc.stdout.readline()
                    if not raw_line:
                        break
                    captured += len(raw_line)
                    if captured > capture_budget:
                        matches.truncated = True
                        matches.truncation_reason = "byte_budget"
                        await self._terminate_process(proc)
                        return
                    if len(raw_line) > line_limit * 8 + 16 * 1024:
                        matches.truncated = True
                        matches.truncation_reason = "line_budget"
                        continue
                    try:
                        obj = json.loads(raw_line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if obj.get("type") != "match":
                        continue
                    data = obj.get("data", {})
                    file_path = data.get("path", {}).get("text", "")
                    if not file_path:
                        continue
                    try:
                        safe_path = resolve_within_root(authority_root, file_path)
                    except (OSError, PermissionError, ValueError):
                        continue
                    file_path = str(safe_path)
                    line_text = data.get("lines", {}).get("text", "").rstrip("\n\r")
                    if len(line_text) > line_limit:
                        line_text = line_text[:line_limit] + "..."
                        matches.truncated = True
                        matches.truncation_reason = matches.truncation_reason or "line_budget"
                    if file_path not in mod_time_cache:
                        try:
                            mod_time_cache[file_path] = os.path.getmtime(file_path)
                        except OSError:
                            mod_time_cache[file_path] = 0.0
                    submatches = []
                    for sm in data.get("submatches", []):
                        text = sm.get("match", {}).get("text", "")[:line_limit]
                        submatches.append({
                            "start": sm.get("start", 0),
                            "end": min(sm.get("end", len(text)), line_limit),
                            "match": {"text": text},
                        })
                    matches.append(GrepMatch(
                        file_path=file_path,
                        line_number=data.get("line_number", 0),
                        line_text=line_text,
                        absolute_offset=data.get("absolute_offset", 0),
                        submatches=submatches,
                        mod_time=mod_time_cache[file_path],
                    ))
                    if max_count and len(matches) >= max_count:
                        matches.truncated = True
                        matches.truncation_reason = "result_limit"
                        await self._terminate_process(proc)
                        return

            try:
                await asyncio.wait_for(consume_stdout(), timeout=timeout)
                if proc.returncode is None:
                    await asyncio.wait_for(proc.wait(), timeout=max(0.1, timeout))
            except asyncio.TimeoutError as exc:
                await self._terminate_process(proc)
                raise SearchTimeoutError(
                    f"Ripgrep search timed out after {timeout:.2f}s"
                ) from exc
            finally:
                if not stderr_task.done():
                    stderr_task.cancel()
                try:
                    stderr = await stderr_task
                except asyncio.CancelledError:
                    stderr = b""

            if not matches.truncated and proc.returncode not in (0, 1):
                err = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"Ripgrep failed (exit {proc.returncode}): {err}")

            matches.sort(key=lambda m: m.mod_time, reverse=True)
            logger.debug(f"Ripgrep search finished: pattern='{pattern}', found {len(matches)} matches")
            return matches

        except FileNotFoundError as e:
            raise RuntimeError("ripgrep (rg) not found in PATH") from e
        except SearchTimeoutError:
            raise
        except Exception as e:
            raise RuntimeError(f"Ripgrep search failed: {e}") from e

    @staticmethod
    async def _terminate_process(proc) -> None:
        """Terminate rg and its process group, escalating if it does not exit."""
        if proc.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except (OSError, ProcessLookupError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.5)
            return
        except asyncio.TimeoutError:
            pass
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass
        await proc.wait()


def _pygrep_worker_main() -> int:
    """Subprocess entry point used to make Python ``re`` preemptible."""

    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        results = asyncio.run(PygrepSearcher()._search_inline(**payload))
        sys.stdout.write(
            json.dumps(
                {
                    "matches": [asdict(item) for item in results],
                    "truncated": bool(getattr(results, "truncated", False)),
                    "truncation_reason": getattr(
                        results, "truncation_reason", None
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 0


if __name__ == "__main__" and "--pygrep-worker" in sys.argv:
    raise SystemExit(_pygrep_worker_main())
