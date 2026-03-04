# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""File namespace: sandbox.file.read_file, write_file, etc."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from aworld.sandbox.namespaces.base import ToolNamespace, resolve_service_name

if TYPE_CHECKING:
    from aworld.sandbox.implementations.sandbox import Sandbox


class FileNamespace(ToolNamespace):
    """File operations. Use sandbox.file.read_file(), etc."""

    def __init__(self, sandbox: "Sandbox"):
        service_name = resolve_service_name(sandbox, "filesystem")
        super().__init__(sandbox, service_name)

    async def read_file(
        self,
        path: str,
        head: Optional[int] = None,
        tail: Optional[int] = None,
        output: str = "text",
    ) -> Dict[str, Any]:
        """Read file. output: 'text' or 'base64'. Returns JSON with type and content/base64."""
        return await self._call_tool("read_file", path=path, head=head, tail=tail, output=output)

    async def write_file(self, path: str, content: str) -> Dict[str, Any]:
        """Write content to file."""
        return await self._call_tool("write_file", path=path, content=content)

    async def edit_file(
        self,
        path: str,
        start_line: int,
        end_line: int,
        new_content: str = "",
        dryRun: bool = False,
    ) -> Dict[str, Any]:
        """
        Edit file by line range [start_line, end_line] (1-based, inclusive).

        - start_line / end_line are line numbers (starting from 1, inclusive at both ends)
        - Empty new_content means deleting the specified lines
        - When dryRun=True, only a git-style diff is returned and nothing is written to disk
        """
        return await self._call_tool(
            "edit_file",
            path=path,
            start_line=start_line,
            end_line=end_line,
            new_content=new_content,
            dryRun=dryRun,
        )

    async def upload_file(self, source_path: str, target_path: str) -> Dict[str, Any]:
        """Copy file from source_path to target_path (target must be in allowed directories)."""
        return await self._call_tool("upload_file", source_path=source_path, target_path=target_path)

    async def download_file(self, path: str) -> Dict[str, Any]:
        """Download file; returns JSON with base64, mimeType, fileName."""
        return await self._call_tool("download_file", path=path)

    async def read_media_file(self, path: str) -> Dict[str, Any]:
        """Read image or audio file as base64. Returns JSON with type (image/audio/blob), data (base64), mimeType."""
        return await self._call_tool("read_media_file", path=path)

    async def parse_file(
        self,
        file_path: str,
        file_type: str,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Parse document to Markdown. output_path optional; default workspace / {stem}.md."""
        return await self._call_tool(
            "parse_file",
            file_path=file_path,
            file_type=file_type,
            output_path=output_path,
        )

    async def create_directory(self, path: str) -> Dict[str, Any]:
        """Create directory."""
        return await self._call_tool("create_directory", path=path)

    async def list_directory(self, path: str) -> Dict[str, Any]:
        """List directory contents."""
        return await self._call_tool("list_directory", path=path)

    async def move_file(self, source: str, destination: str) -> Dict[str, Any]:
        """Move file from source to destination."""
        return await self._call_tool("move_file", source=source, destination=destination)

    async def list_allowed_directories(self) -> Dict[str, Any]:
        """List allowed workspace directories."""
        return await self._call_tool("list_allowed_directories")

    async def search_content(
        self,
        path: str,
        pattern: str,
        max_matches: Optional[int] = None,
        max_per_file: Optional[int] = None,
        before: int = 0,
        after: int = 0,
    ) -> Dict[str, Any]:
        """
        Search file or directory for lines matching a regex pattern.

        - path can be a single file or a directory (directory is searched recursively)
        - max_matches / max_per_file cap the number of matches
        - before / after control how many context lines are included around each match
        """
        return await self._call_tool(
            "search_content",
            path=path,
            pattern=pattern,
            max_matches=max_matches,
            max_per_file=max_per_file,
            before=before,
            after=after,
        )

    async def search_files(
        self,
        path: str,
        pattern: str,
        exclude_patterns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Search for files matching glob pattern under path. Recursively searches subdirectories. exclude_patterns: optional list of glob patterns to exclude."""
        return await self._call_tool(
            "search_files",
            path=path,
            pattern=pattern,
            excludePatterns=exclude_patterns or [],
        )
