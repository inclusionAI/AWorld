"""

Provides high-performance text search and file discovery based on grep.
Also provides a Python-based Pygrep implementation as an alternative implementation.
"""

import asyncio
import fnmatch
import os
import re
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

class PygrepSearcher:
    """
    Grep‑like searcher implemented in pure Python.

    Uses Python's ``re`` module and filesystem traversal to implement text search
    as a fallback when grep is not available.
    Provides the same interface as ``grepSearcher`` and can be used as a drop‑in replacement.
    """

    def __init__(self):
        """Initialize the Pygrep searcher"""
        pass

    async def ensure_installed(self):
        """Ensure the searcher is available (Python implementation needs no installation)"""
        pass

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

        # Traverse files
        def search_files():
            nonlocal match_count
            for root, dirs, files in os.walk(search_path, followlinks=follow_symlinks):
                # Filter directories
                dirs[:] = [d for d in dirs if search_hidden or not d.startswith('.')]
                
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
                    
                    try:
                        # Read file content
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()
                            absolute_offset = 0
                            
                            for line_num, line in enumerate(lines, start=1):
                                # Check again if we have reached the maximum number of matches
                                if max_count and match_count >= max_count:
                                    break
                                
                                line_text = line.rstrip('\n\r')
                                
                                # Search for matches in this line
                                for match in regex.finditer(line_text):
                                    # Extract sub‑matches for capturing groups
                                    submatches = []
                                    for i, group in enumerate(match.groups(), start=1):
                                        if group is not None:
                                            submatches.append({
                                                'start': match.start(i),
                                                'end': match.end(i),
                                                'match': {'text': group}
                                            })
                                    
                                    # Add the main match at the beginning
                                    submatches.insert(0, {
                                        'start': match.start(),
                                        'end': match.end(),
                                        'match': {'text': match.group()}
                                    })
                                    
                                    # Get file modification time
                                    try:
                                        mod_time = os.path.getmtime(file_path)
                                    except OSError:
                                        mod_time = 0.0
                                    
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
                                
                                # Update absolute byte offset (including newline bytes)
                                absolute_offset += len(line.encode('utf-8'))
                                
                    except (UnicodeDecodeError, PermissionError, OSError) as e:
                        logger.debug(f"Skip file {file_path}: {e}")
                        continue

        # Run the search in a thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, search_files)

        # Sort by modification time
        matches.sort(key=lambda m: m.mod_time, reverse=True)
        
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