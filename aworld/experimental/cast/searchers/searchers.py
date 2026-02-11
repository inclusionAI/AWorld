"""
Search Tool Implementations
===========================

Implements specific search tools: Grep, Glob, Read, etc.
Based on opencode design, provides high-performance search capabilities.
"""

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

from .engine import Searcher, SearchParams, SearchResult, SearchType
from .utils import PygrepSearcher
from ..utils import logger


class GrepSearcher(Searcher):
    """
    Grep Content Search Tool

    Uses Pygrep (Python-based grep) for text content search.
    Based on opencode's grep.ts implementation.
    """

    def __init__(self, root_path: Optional[Path] = None):
        self.root_path = root_path or Path.cwd()
        self.searcher = PygrepSearcher()
        self.max_line_length = 2000

    def get_search_type(self) -> SearchType:
        return SearchType.GREP

    def validate_params(self, params: SearchParams) -> bool:
        """Validate search parameters"""
        if not params.pattern:
            logger.error("Grep search requires pattern parameter")
            return False
        return True

    async def search(self, params: SearchParams) -> SearchResult:
        """Execute Grep search"""
        start_time = time.time()

        if not self.validate_params(params):
            raise ValueError("Invalid search parameters")

        # Execute search synchronously
        matches = await self._async_search(params)

        execution_time = time.time() - start_time

        # Sort by modification time
        matches.sort(key=lambda m: m.get('mod_time', 0), reverse=True)

        # Apply result limit
        limit = params.max_results
        truncated = len(matches) > limit
        final_matches = matches[:limit] if truncated else matches

        # Format output
        output_lines = [f"Found {len(final_matches)} matches"]

        if final_matches:
            current_file = ""
            for match in final_matches:
                file_path = match['file_path']
                if current_file != file_path:
                    if current_file:
                        output_lines.append("")
                    current_file = file_path
                    output_lines.append(f"{file_path}:")

                line_text = match['line_text']
                if len(line_text) > self.max_line_length:
                    line_text = line_text[:self.max_line_length] + "..."

                output_lines.append(f"  Line {match['line_number']}: {line_text}")
        else:
            output_lines.append("No matching files found")

        if truncated:
            output_lines.append("")
            output_lines.append("(Results truncated. Consider using a more specific path or pattern.)")

        return SearchResult(
            title=params.pattern,
            search_type=SearchType.GREP,
            matches=final_matches,
            metadata={
                "matches": len(final_matches),
                "truncated": truncated,
                "total_found": len(matches)
            },
            output="\n".join(output_lines),
            truncated=truncated,
            total_count=len(matches),
            execution_time=execution_time
        )

    async def _async_search(self, params: SearchParams) -> List[Dict[str, Any]]:
        """Execute search asynchronously"""
        search_path = params.path or str(self.root_path)

        try:
            pygrep_matches = await self.searcher.search(
                pattern=params.pattern,
                path=search_path,
                include_patterns=params.include_patterns,
                max_count=params.max_results,
                context_lines=params.context_lines,
                case_sensitive=params.case_sensitive,
                follow_symlinks=params.follow_symlinks,
                search_hidden=params.search_hidden
            )

            matches = []
            for pygrep_match in pygrep_matches:
                matches.append({
                    'file_path': pygrep_match.file_path,
                    'line_number': pygrep_match.line_number,
                    'line_text': pygrep_match.line_text,
                    'mod_time': pygrep_match.mod_time,
                    'absolute_offset': pygrep_match.absolute_offset,
                    'submatches': pygrep_match.submatches
                })

            return matches

        except Exception as e:
            logger.error(f"Pygrep search failed: {e}")
            return []

    def set_root_path(self, path: Path):
        """Set root path"""
        self.root_path = path


class GlobSearcher(Searcher):
    """
    Glob File Pattern Matching Tool

    Uses Pygrep for file discovery.
    Based on opencode's glob.ts implementation.
    """

    def __init__(self, root_path: Optional[Path] = None):
        self.root_path = root_path or Path.cwd()
        self.searcher = PygrepSearcher()

    def get_search_type(self) -> SearchType:
        return SearchType.GLOB

    def validate_params(self, params: SearchParams) -> bool:
        """Validate search parameters"""
        if not params.pattern:
            logger.error("Glob search requires pattern parameter")
            return False
        return True

    async def search(self, params: SearchParams) -> SearchResult:
        """Execute Glob search"""
        start_time = time.time()

        if not self.validate_params(params):
            raise ValueError("Invalid search parameters")

        # Execute search synchronously
        file_paths = await self._async_search(params)

        execution_time = time.time() - start_time

        # Get file information and sort
        files_with_mtime = []
        for file_path in file_paths:
            try:
                full_path = Path(file_path)
                if full_path.exists():
                    mtime = full_path.stat().st_mtime
                    files_with_mtime.append({
                        'path': str(full_path),
                        'mtime': mtime
                    })
            except OSError:
                # Ignore inaccessible files
                continue

        # Sort by modification time
        files_with_mtime.sort(key=lambda f: f['mtime'], reverse=True)

        # Apply result limit
        limit = params.max_results
        truncated = len(files_with_mtime) > limit
        final_files = files_with_mtime[:limit] if truncated else files_with_mtime

        # Format output
        output_lines = []
        if not final_files:
            output_lines.append("No matching files found")
        else:
            output_lines.extend([f['path'] for f in final_files])
            if truncated:
                output_lines.append("")
                output_lines.append("(Results truncated. Consider using a more specific path or pattern.)")

        search_dir = params.path or str(self.root_path)
        title = os.path.relpath(search_dir, self.root_path)

        return SearchResult(
            title=title,
            search_type=SearchType.GLOB,
            matches=final_files,
            metadata={
                "count": len(final_files),
                "truncated": truncated,
                "total_found": len(files_with_mtime)
            },
            output="\n".join(output_lines),
            truncated=truncated,
            total_count=len(files_with_mtime),
            execution_time=execution_time
        )

    async def _async_search(self, params: SearchParams) -> List[str]:
        """Execute file discovery asynchronously"""
        search_path = params.path or str(self.root_path)

        try:
            # Build include patterns list
            include_patterns = [params.pattern]
            if params.include_patterns:
                include_patterns.extend(params.include_patterns)

            file_paths = await self.searcher.find_files(
                path=search_path,
                include_patterns=include_patterns,
                max_depth=params.max_depth,
                follow_symlinks=params.follow_symlinks,
                search_hidden=params.search_hidden
            )

            return file_paths

        except Exception as e:
            logger.error(f"File discovery failed: {e}")
            return []

    def set_root_path(self, path: Path):
        """Set root path"""
        self.root_path = path


class ReadSearcher(Searcher):
    """
    File Reading Tool

    Reads and processes file content, supports binary file detection and content truncation.
    Based on opencode's read.ts implementation.
    """

    def __init__(self, root_path: Optional[Path] = None):
        self.root_path = root_path or Path.cwd()
        self.default_read_limit = 2000
        self.max_line_length = 2000
        self.max_bytes = 50 * 1024

    def get_search_type(self) -> SearchType:
        return SearchType.READ

    def validate_params(self, params: SearchParams) -> bool:
        """Validate search parameters"""
        if not params.path:
            logger.error("Read tool requires path parameter")
            return False
        return True

    async def search(self, params: SearchParams) -> SearchResult:
        """Execute file reading"""
        start_time = time.time()

        if not self.validate_params(params):
            raise ValueError("Invalid search parameters")

        file_path = Path(params.path)
        if not file_path.is_absolute():
            file_path = self.root_path / file_path

        # Try to get relative path, but fall back to absolute path if not in subpath
        try:
            title = str(file_path.relative_to(self.root_path))
        except ValueError:
            # File is not in root_path subpath, use absolute path as title
            title = str(file_path)

        try:
            # Check if file exists
            if not file_path.exists():
                return self._create_error_result(
                    title, f"File does not exist: {file_path}", start_time
                )

            # Check if file is binary
            if self._is_binary_file(file_path):
                return self._create_error_result(
                    title, f"Cannot read binary file: {file_path}", start_time
                )

            # Read file content
            try:
                content = file_path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                try:
                    content = file_path.read_text(encoding='utf-8', errors='ignore')
                except Exception as e:
                    return self._create_error_result(
                        title, f"Failed to read file: {e}", start_time
                    )

            lines = content.split('\n')

            # Apply offset and limit
            limit = params.limit or self.default_read_limit
            offset = params.offset

            raw_lines = []
            bytes_count = 0
            truncated_by_bytes = False

            start_idx = offset
            end_idx = min(len(lines), offset + limit)

            for i in range(start_idx, end_idx):
                line = lines[i]
                if len(line) > self.max_line_length:
                    line = line[:self.max_line_length] + "..."

                line_bytes = len(line.encode('utf-8')) + (1 if raw_lines else 0)
                if bytes_count + line_bytes > self.max_bytes:
                    truncated_by_bytes = True
                    break

                raw_lines.append(line)
                bytes_count += line_bytes

            # Format output
            formatted_lines = []
            for idx, line in enumerate(raw_lines):
                line_num = offset + idx + 1
                formatted_lines.append(f"{line_num:5d}→{line}")

            output = "<file>\n" + "\n".join(formatted_lines)

            # Add truncation information
            total_lines = len(lines)
            last_read_line = offset + len(raw_lines)
            has_more_lines = total_lines > last_read_line
            truncated = has_more_lines or truncated_by_bytes

            if truncated_by_bytes:
                output += f"\n\n(Output truncated at {self.max_bytes} bytes. Use 'offset' parameter to read content after line {last_read_line})"
            elif has_more_lines:
                output += f"\n\n(File has more lines. Use 'offset' parameter to read content after line {last_read_line})"
            else:
                output += f"\n\n(End of file - total {total_lines} lines)"

            output += "\n</file>"

            execution_time = time.time() - start_time

            return SearchResult(
                title=title,
                search_type=SearchType.READ,
                matches=[{
                    'file_path': str(file_path),
                    'lines_read': len(raw_lines),
                    'total_lines': total_lines,
                    'offset': offset,
                    'bytes_read': bytes_count
                }],
                metadata={
                    "preview": "\n".join(raw_lines[:20]),
                    "truncated": truncated,
                    "lines_read": len(raw_lines),
                    "total_lines": total_lines
                },
                output=output,
                truncated=truncated,
                total_count=total_lines,
                execution_time=execution_time
            )

        except Exception as e:
            return self._create_error_result(title, f"Error occurred while reading file: {e}", start_time)

    def _create_error_result(self, title: str, error_msg: str, start_time: float) -> SearchResult:
        """Create error result"""
        execution_time = time.time() - start_time
        return SearchResult(
            title=title,
            search_type=SearchType.READ,
            matches=[],
            metadata={"error": error_msg},
            output=error_msg,
            truncated=False,
            total_count=0,
            execution_time=execution_time
        )

    def _is_binary_file(self, file_path: Path) -> bool:
        """Detect if file is binary"""
        # Check file extension
        binary_extensions = {
            '.zip', '.tar', '.gz', '.exe', '.dll', '.so', '.class', '.jar',
            '.war', '.7z', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            '.odt', '.ods', '.odp', '.bin', '.dat', '.obj', '.o', '.a',
            '.lib', '.wasm', '.pyc', '.pyo'
        }

        if file_path.suffix.lower() in binary_extensions:
            return True

        # Check file content
        try:
            stat = file_path.stat()
            if stat.st_size == 0:
                return False

            buffer_size = min(4096, stat.st_size)
            with file_path.open('rb') as f:
                chunk = f.read(buffer_size)

            if not chunk:
                return False

            # Check for null bytes
            if b'\x00' in chunk:
                return True

            # Calculate non-printable character ratio
            non_printable = 0
            for byte in chunk:
                if byte < 9 or (byte > 13 and byte < 32):
                    non_printable += 1

            # If more than 30% are non-printable characters, consider it binary
            return (non_printable / len(chunk)) > 0.3

        except Exception:
            return True

    def set_root_path(self, path: Path):
        """Set root path"""
        self.root_path = path