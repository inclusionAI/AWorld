"""
Search Tool Implementations
===========================

Implements specific search tools: Grep, Glob, Read, etc.
Based on opencode design, provides high-performance search capabilities.
"""

import base64
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

from .engine import Searcher, SearchParams, SearchResult, SearchType
from .utils import (
    DEFAULT_MAX_CAPTURE_BYTES,
    DEFAULT_MAX_FILE_RESULTS,
    DEFAULT_MAX_SEARCH_BYTES,
    DEFAULT_SEARCH_TIMEOUT_SECONDS,
    BoundedResults,
    _read_bounded_binary_line,
    PygrepSearcher,
    grep_search_with_fallback,
)
from .path_policy import canonical_root, resolve_within_root
from ..utils import logger


class GrepSearcher(Searcher):
    """
    Grep Content Search Tool

    Uses Ripgrep when available (10-100x faster), automatically falls back to Pygrep
    when ripgrep is not installed or errors.
    Based on opencode's grep.ts implementation.
    """

    def __init__(self, root_path: Optional[Path] = None):
        self.root_path = canonical_root(root_path or Path.cwd())
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

        matches = await self._async_search(params)

        execution_time = time.time() - start_time

        # Apply result limit (underlying searcher already sorts by mod_time)
        limit = min(max(1, int(params.max_results)), DEFAULT_MAX_FILE_RESULTS)
        producer_truncated = bool(getattr(matches, "truncated", False))
        truncated = producer_truncated or len(matches) > limit
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

        search_path = params.path or str(self.root_path)
        pattern = params.pattern or ""
        pattern_display = f"{pattern[:50]}{'...' if len(pattern) > 50 else ''}"
        trunc_info = f", truncated to {limit}" if truncated else ""
        logger.info(
            f"Grep search: pattern='{pattern_display}', path='{search_path}', "
            f"found {len(matches)} matches in {execution_time:.2f}s{trunc_info}"
        )

        return SearchResult(
            title=params.pattern,
            search_type=SearchType.GREP,
            matches=final_matches,
            metadata={
                "matches": len(final_matches),
                "truncated": truncated,
                "total_found": len(matches),
                "total_found_exact": not truncated,
                "truncation_reason": (
                    getattr(matches, "truncation_reason", None)
                    or ("result_limit" if len(matches) > limit else None)
                ),
            },
            output="\n".join(output_lines),
            truncated=truncated,
            total_count=len(matches),
            execution_time=execution_time
        )

    async def _async_search(self, params: SearchParams) -> List[Dict[str, Any]]:
        """Execute search: Ripgrep when available, auto-fallback to Pygrep on failure."""
        search_path = resolve_within_root(
            self.root_path, params.path or self.root_path
        )
        limit = min(max(1, int(params.max_results)), DEFAULT_MAX_FILE_RESULTS)
        raw = await grep_search_with_fallback(
            pattern=params.pattern,
            path=str(search_path),
            include_patterns=params.include_patterns,
            max_count=limit + 1,
            context_lines=params.context_lines,
            case_sensitive=params.case_sensitive,
            follow_symlinks=params.follow_symlinks,
            search_hidden=params.search_hidden,
            timeout_seconds=params.timeout_seconds or DEFAULT_SEARCH_TIMEOUT_SECONDS,
            max_scan_bytes=params.max_bytes or DEFAULT_MAX_SEARCH_BYTES,
            max_output_bytes=params.max_bytes or DEFAULT_MAX_CAPTURE_BYTES,
            max_line_length=min(max(1, params.max_line_length), self.max_line_length),
        )
        converted = BoundedResults(
            ({
                'file_path': m.file_path,
                'line_number': m.line_number,
                'line_text': m.line_text,
                'mod_time': m.mod_time,
                'absolute_offset': m.absolute_offset,
                'submatches': m.submatches,
            } for m in raw),
            truncated=getattr(raw, "truncated", False),
            reason=getattr(raw, "truncation_reason", None),
        )
        return converted

    def set_root_path(self, path: Path):
        """Set root path"""
        self.root_path = canonical_root(path)


class GlobSearcher(Searcher):
    """
    Glob File Pattern Matching Tool

    Uses Pygrep for file discovery.
    Based on opencode's glob.ts implementation.
    """

    def __init__(self, root_path: Optional[Path] = None):
        self.root_path = canonical_root(root_path or Path.cwd())
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
        limit = min(max(1, int(params.max_results)), DEFAULT_MAX_FILE_RESULTS)
        producer_truncated = bool(getattr(file_paths, "truncated", False))
        truncated = producer_truncated or len(files_with_mtime) > limit
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
                "total_found": len(files_with_mtime),
                "total_found_exact": not truncated,
                "truncation_reason": (
                    getattr(file_paths, "truncation_reason", None)
                    or ("result_limit" if len(files_with_mtime) > limit else None)
                ),
            },
            output="\n".join(output_lines),
            truncated=truncated,
            total_count=len(files_with_mtime),
            execution_time=execution_time
        )

    async def _async_search(self, params: SearchParams) -> List[str]:
        """Execute file discovery asynchronously"""
        search_path = resolve_within_root(
            self.root_path, params.path or self.root_path
        )

        # Build include patterns list
        include_patterns = [params.pattern]
        if params.include_patterns:
            include_patterns.extend(params.include_patterns)

        return await self.searcher.find_files(
            path=str(search_path),
            include_patterns=include_patterns,
            max_depth=params.max_depth,
            follow_symlinks=params.follow_symlinks,
            search_hidden=params.search_hidden,
            max_count=min(max(1, int(params.max_results)), DEFAULT_MAX_FILE_RESULTS) + 1,
            timeout_seconds=params.timeout_seconds or DEFAULT_SEARCH_TIMEOUT_SECONDS,
        )

    def set_root_path(self, path: Path):
        """Set root path"""
        self.root_path = canonical_root(path)


class ReadSearcher(Searcher):
    """
    File Reading Tool

    Reads and processes file content, supports binary file detection and content truncation.
    Based on opencode's read.ts implementation.
    """

    def __init__(self, root_path: Optional[Path] = None):
        self.root_path = canonical_root(root_path or Path.cwd())
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

        file_path = resolve_within_root(self.root_path, params.path)
        title = str(file_path.relative_to(self.root_path))

        try:
            # Check if file exists
            if not file_path.exists():
                return self._create_error_result(
                    title, f"File does not exist: {file_path}", start_time
                )

            # Check if file is multimedia (image, audio, video) - return base64
            mime_type = self._get_multimedia_mime_type(file_path)
            if mime_type:
                return await self._read_multimedia_file(
                    file_path, title, mime_type, start_time
                )

            # Check if file is binary (non-multimedia)
            if self._is_binary_file(file_path):
                return self._create_error_result(
                    title, f"Cannot read binary file: {file_path}", start_time
                )

            limit = max(1, int(params.limit or self.default_read_limit))
            offset = max(0, int(params.offset))
            output_budget = min(
                self.max_bytes,
                max(1, int(params.max_bytes)) if params.max_bytes is not None else self.max_bytes,
            )
            scan_budget = DEFAULT_MAX_SEARCH_BYTES
            line_limit = min(max(1, int(params.max_line_length)), self.max_line_length)

            raw_lines = []
            bytes_count = 0
            scanned_bytes = 0
            total_lines = 0
            truncated_by_bytes = False
            truncated_line = False
            scan_truncated = False
            eof_reached = False
            last_ended_with_newline = False

            def collect_line(line: str, line_index: int, was_truncated: bool = False) -> None:
                nonlocal bytes_count, truncated_by_bytes, truncated_line
                if was_truncated and offset <= line_index < offset + limit:
                    truncated_line = True
                if not (offset <= line_index < offset + limit) or truncated_by_bytes:
                    return
                line_bytes = len(line.encode('utf-8')) + (1 if raw_lines else 0)
                if bytes_count + line_bytes > output_budget:
                    truncated_by_bytes = True
                    return
                raw_lines.append(line)
                bytes_count += line_bytes

            with file_path.open('rb') as handle:
                while scanned_bytes < scan_budget:
                    item = _read_bounded_binary_line(
                        handle,
                        content_limit=line_limit,
                        scan_limit=scan_budget - scanned_bytes,
                    )
                    if item is None:
                        eof_reached = True
                        break
                    line, consumed, complete, was_truncated, ended_with_newline = item
                    collect_line(line, total_lines, was_truncated)
                    total_lines += 1
                    scanned_bytes += consumed
                    last_ended_with_newline = ended_with_newline
                    if not complete:
                        scan_truncated = True
                        break

            # Preserve the previous split('\n') semantics for small files:
            # an empty file and a trailing newline expose a final empty line.
            if eof_reached and (total_lines == 0 or last_ended_with_newline):
                collect_line("", total_lines)
                total_lines += 1

            if not eof_reached and scanned_bytes >= scan_budget:
                scan_truncated = True

            # Format output
            formatted_lines = []
            for idx, line in enumerate(raw_lines):
                line_num = offset + idx + 1
                formatted_lines.append(f"{line_num:5d}→{line}")

            output = "<file>\n" + "\n".join(formatted_lines)

            # Add truncation information
            last_read_line = offset + len(raw_lines)
            has_more_lines = total_lines > last_read_line
            truncated = has_more_lines or truncated_by_bytes or truncated_line or scan_truncated

            if truncated_by_bytes:
                output += f"\n\n(Output truncated at {output_budget} bytes. Use 'offset' parameter to read content after line {last_read_line})"
            elif scan_truncated:
                output += f"\n\n(Read scan truncated at {scan_budget} bytes; total line count is a lower bound.)"
            elif truncated_line:
                output += f"\n\n(Long line truncated at {line_limit} characters.)"
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
                    'bytes_read': bytes_count,
                    'bytes_scanned': scanned_bytes,
                }],
                metadata={
                    "preview": "\n".join(raw_lines[:20]),
                    "truncated": truncated,
                    "lines_read": len(raw_lines),
                    "total_lines": total_lines,
                    "total_lines_exact": not scan_truncated,
                    "truncation_reason": (
                        "scan_byte_budget" if scan_truncated else
                        "output_byte_budget" if truncated_by_bytes else
                        "line_budget" if truncated_line else
                        "line_limit" if has_more_lines else None
                    ),
                },
                output=output,
                truncated=truncated,
                total_count=total_lines,
                execution_time=execution_time
            )

        except Exception as e:
            return self._create_error_result(title, f"Error occurred while reading file: {e}", start_time)

    def _get_multimedia_mime_type(self, file_path: Path) -> Optional[str]:
        """Return MIME type if file is multimedia (image/audio/video), else None."""
        ext = file_path.suffix.lower()
        multimedia_extensions = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
            '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
            '.ico': 'image/x-icon', '.tiff': 'image/tiff', '.tif': 'image/tiff',
            '.mp3': 'audio/mp3', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
            '.m4a': 'audio/mp4', '.flac': 'audio/flac', '.aac': 'audio/aac',
            '.mp4': 'video/mp4', '.webm': 'video/webm', '.avi': 'video/x-msvideo',
            '.mov': 'video/quicktime', '.mkv': 'video/x-matroska', '.m4v': 'video/x-m4v',
        }
        return multimedia_extensions.get(ext)

    async def _read_multimedia_file(
        self, file_path: Path, title: str, mime_type: str, start_time: float
    ) -> SearchResult:
        """Read multimedia file as binary and return base64 data URI (plain text, not JSON)."""
        try:
            size_bytes = file_path.stat().st_size
            media_limit = max(1, int(os.environ.get("CAST_MEDIA_SIZE_LIMIT_KB", "50"))) * 1024
            if size_bytes > media_limit:
                return self._create_error_result(
                    title,
                    f"Multimedia file size ({size_bytes} bytes) exceeds limit ({media_limit} bytes)",
                    start_time,
                )
            raw_bytes = file_path.read_bytes()
            b64 = base64.b64encode(raw_bytes).decode('ascii')
            data_uri = f"data:{mime_type};base64,{b64}"
            execution_time = time.time() - start_time
            return SearchResult(
                title=title,
                search_type=SearchType.READ,
                matches=[{
                    'file_path': str(file_path),
                    'mime_type': mime_type,
                    'size_bytes': len(raw_bytes),
                    'is_multimedia': True,
                }],
                metadata={
                    "is_multimedia": True,
                    "mime_type": mime_type,
                    "size_bytes": len(raw_bytes),
                },
                output=data_uri,
                truncated=False,
                total_count=1,
                execution_time=execution_time
            )
        except Exception as e:
            return self._create_error_result(
                title, f"Failed to read multimedia file: {e}", start_time
            )

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
        self.root_path = canonical_root(path)
