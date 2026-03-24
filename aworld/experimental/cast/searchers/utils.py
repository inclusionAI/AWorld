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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..utils import logger


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
                    search_hidden: bool = True) -> List[GrepMatch]:
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
        search_path = Path(path)
        if not search_path.exists():
            raise ValueError(f"Search path does not exist: {path}")

        # Compile regular expression
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regular expression pattern: {pattern}, error: {e}")

        matches = []
        match_count = 0
        files_scanned = [0]  # mutable for nested function
        bytes_scanned = [0]

        # Traverse files
        def search_files():
            nonlocal match_count
            for root, dirs, files in os.walk(search_path, followlinks=follow_symlinks):
                # Filter directories: hidden, and default exclude list
                dirs[:] = [
                    d for d in dirs
                    if (search_hidden or not d.startswith('.'))
                    and not self._should_exclude_dir(d)
                ]

                for file_name in files:
                    # Skip hidden files if required
                    if not search_hidden and file_name.startswith('.'):
                        continue
                    
                    file_path = Path(root) / file_name
                    
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
                        # Stream file line-by-line (avoids loading entire file into memory)
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            absolute_offset = 0
                            for line_num, line in enumerate(f, start=1):
                                if max_count and match_count >= max_count:
                                    break

                                line_text = line.rstrip('\n\r')
                                bytes_scanned[0] += len(line.encode('utf-8'))

                                for match in regex.finditer(line_text):
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

                                absolute_offset += len(line.encode('utf-8'))

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
                        search_hidden: bool = True) -> List[str]:
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
        search_path = Path(path)
        if not search_path.exists():
            raise ValueError(f"Search path does not exist: {path}")

        file_paths = []

        def find_files_recursive(current_path: Path, current_depth: int = 0):
            # Check depth limit
            if max_depth is not None and current_depth > max_depth:
                return
            
            try:
                # Walk current directory
                for item in current_path.iterdir():
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
                            item = item.resolve()
                        except (OSError, RuntimeError):
                            continue
                    
                    if item.is_file():
                        # Check whether it matches the include patterns
                        if self._should_include_file(item, include_patterns):
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
    )
    rg = RipgrepSearcher()
    py = PygrepSearcher()
    try:
        return await rg.search(**search_kw)
    except Exception as e:
        logger.info(f"Ripgrep unavailable or failed, using Pygrep: {e}")
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
                    search_hidden: bool = True) -> List[GrepMatch]:
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
        cmd = ["rg", "--json", "-n", "--no-heading", "--no-column"]
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
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode not in (0, 1):
                err = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"Ripgrep failed (exit {proc.returncode}): {err}")

            # ripgrep exit 0 = matches found, 1 = no matches
            matches: List[GrepMatch] = []
            mod_time_cache: Dict[str, float] = {}

            for line in stdout.decode("utf-8", errors="replace").splitlines():
                if max_count and len(matches) >= max_count:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "match":
                    continue
                data = obj.get("data", {})
                path_data = data.get("path", {})
                file_path = path_data.get("text", "")
                if not file_path:
                    continue
                lines_data = data.get("lines", {})
                line_text = lines_data.get("text", "").rstrip("\n\r")
                line_number = data.get("line_number", 0)
                absolute_offset = data.get("absolute_offset", 0)
                submatches_data = data.get("submatches", [])

                if file_path not in mod_time_cache:
                    try:
                        mod_time_cache[file_path] = os.path.getmtime(file_path)
                    except OSError:
                        mod_time_cache[file_path] = 0.0
                mod_time = mod_time_cache[file_path]

                submatches = []
                for sm in submatches_data:
                    m = sm.get("match", {})
                    txt = m.get("text", "")
                    start = sm.get("start", 0)
                    end = sm.get("end", len(txt))
                    submatches.append({
                        "start": start,
                        "end": end,
                        "match": {"text": txt}
                    })

                matches.append(GrepMatch(
                    file_path=file_path,
                    line_number=line_number,
                    line_text=line_text,
                    absolute_offset=absolute_offset,
                    submatches=submatches,
                    mod_time=mod_time,
                ))

            matches.sort(key=lambda m: m.mod_time, reverse=True)
            logger.debug(f"Ripgrep search finished: pattern='{pattern}', found {len(matches)} matches")
            return matches

        except FileNotFoundError as e:
            raise RuntimeError("ripgrep (rg) not found in PATH") from e
        except Exception as e:
            raise RuntimeError(f"Ripgrep search failed: {e}") from e