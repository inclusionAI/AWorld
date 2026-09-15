"""Filesystem MCP Server - powered by FastMCP"""

import asyncio
import os
import sys
import json
import logging
from pathlib import Path
from typing import Annotated, Literal, Optional
from fnmatch import fnmatch

from mcp.server import FastMCP
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field

try:  # Package import (tests / installed wheel).
    from .utils.path_utils import (
        validate_path,
        normalize_path,
        resolve_and_require_file,
        require_directory,
        require_regular_file,
    )
    from .utils.document_processor import (
        parse_to_path as parse_file_to_path,
        verify_file_type as parse_verify_file_type,
    )
    from .utils.file_ops import (
        read_file as read_file_content,
        write_file as write_file_content,
        write_file_base64 as write_file_base64_content,
        read_binary_chunk,
        read_text_bounded,
        is_text_file,
        get_mime_and_filename,
        copy_file_binary,
        get_file_stats,
        format_size,
        edit_file_by_line_range,
        search_content as search_content_impl,
        search_files_bounded,
    )
    from .utils.limits import FilesystemLimits, truncation_notice
except ImportError:  # Direct execution by the stdio MCP launcher.
    from utils.path_utils import (
        validate_path,
        normalize_path,
        resolve_and_require_file,
        require_directory,
        require_regular_file,
    )
    from utils.document_processor import (
        parse_to_path as parse_file_to_path,
        verify_file_type as parse_verify_file_type,
    )
    from utils.file_ops import (
        read_file as read_file_content,
        write_file as write_file_content,
        write_file_base64 as write_file_base64_content,
        read_binary_chunk,
        read_text_bounded,
        is_text_file,
        get_mime_and_filename,
        copy_file_binary,
        get_file_stats,
        format_size,
        edit_file_by_line_range,
        search_content as search_content_impl,
        search_files_bounded,
    )
    from utils.limits import FilesystemLimits, truncation_notice

# List of allowed directories for all tools
allowed_directories: list[str] = []


async def set_allowed_directories(dirs: list[str]) -> None:
    """Configure the directories that filesystem tools are allowed to access."""
    global allowed_directories
    allowed_directories = [normalize_path(d) for d in dirs]


def get_allowed_directories() -> list[str]:
    """Return a copy of the currently allowed directories."""
    return allowed_directories.copy()


def _partial_read_metadata(read_result) -> dict:
    """Return additive metadata only when a legacy read is incomplete."""

    if read_result.complete:
        return {}
    return {
        "complete": False,
        "returnedBytes": read_result.returned_bytes,
        "totalBytes": read_result.total_bytes,
        "truncationReason": read_result.truncation_reason,
        "nextLine": read_result.next_line,
    }


def _binary_read_metadata(read_result) -> dict:
    """Return paging metadata for a partial binary result."""

    if read_result.complete and read_result.offset == 0:
        return {}
    return {
        "complete": read_result.complete,
        "offset": read_result.offset,
        "nextOffset": read_result.next_offset,
        "returnedBytes": len(read_result.data),
        "totalBytes": read_result.total_bytes,
        "truncated": not read_result.complete,
    }


def _list_directory_bounded(path: str, limit: int) -> tuple[list[str], bool]:
    """List at most ``limit`` children without following symlinks."""

    rendered: list[str] = []
    truncated = False
    with os.scandir(path) as entries:
        for entry in entries:
            if len(rendered) >= limit:
                truncated = True
                break
            try:
                if entry.is_symlink():
                    prefix = "[SYMLINK]"
                elif entry.is_dir(follow_symlinks=False):
                    prefix = "[DIR]"
                else:
                    prefix = "[FILE]"
            except OSError:
                prefix = "[FILE]"
            rendered.append(f"{prefix} {entry.name}")
    rendered.sort(key=str.casefold)
    return rendered, truncated


# Initialize FastMCP server
# Read log level from environment variable, default to WARNING for clean CLI output
_log_level = os.environ.get("MCP_LOG_LEVEL") or os.environ.get("LOG_LEVEL") or os.environ.get("LOGLEVEL") or "WARNING"
mcp = FastMCP(
    "filesystem-server",
    log_level=_log_level,
    port=8084,
    instructions="Filesystem MCP Server for file operations"
)


# ==================== Enabled MCP tools ====================

@mcp.tool(
    description="Read file content. Use output='text' for text (supports head/tail); use output='base64' for binary. "
    "head: first N lines; tail: last N lines; both: lines head to tail (1-based inclusive). "
    "Binary reads support offset/limit paging. Large results include completeness metadata. "
    "Returns JSON: {\"type\":\"text\",\"content\":\"...\"} or {\"type\":\"base64\",\"base64\":\"...\",\"mimeType\":\"...\",\"fileName\":\"...\"}."
)
async def read_file(
    ctx: Context,
    path: str = Field(description="File path to read"),
    head: Optional[int] = Field(None, description="First N lines, or start line when used with tail"),
    tail: Optional[int] = Field(None, description="Last N lines, or end line when used with head"),
    output: str = Field("text", description="Output format: 'text' or 'base64'"),
    offset: Annotated[int, Field(description="Binary byte offset; only used with output='base64'")] = 0,
    limit: Annotated[Optional[int], Field(description="Binary bytes to return; capped by server policy")] = None,
) -> TextContent:
    """Read file as text or base64; head/tail apply only when file is text (content-based detection)."""
    import base64 as b64
    valid_path = await validate_path(path, allowed_directories)
    require_regular_file(valid_path)
    if output not in ("text", "base64"):
        raise ValueError("output must be 'text' or 'base64'")

    if output == "text":
        if offset != 0 or limit is not None:
            raise ValueError("offset and limit are only supported with output='base64'")
        if not await is_text_file(valid_path):
            raise ValueError("File is not valid UTF-8 text; use output='base64' for binary files")
        read_result = await read_text_bounded(valid_path, head=head, tail=tail)
        payload = {"type": "text", "content": read_result.content}
        payload.update(_partial_read_metadata(read_result))
        return TextContent(type="text", text=json.dumps(payload))

    # output == "base64"
    is_text = await is_text_file(valid_path)
    if is_text and (head is not None or tail is not None):
        if offset != 0 or limit is not None:
            raise ValueError("offset/limit cannot be combined with head/tail")
        read_result = await read_text_bounded(valid_path, head=head, tail=tail)
        b64_data = b64.b64encode(read_result.content.encode("utf-8")).decode("ascii")
        mime_type = "text/plain; charset=utf-8"
        file_name = Path(valid_path).name
        payload = {"type": "base64", "base64": b64_data, "mimeType": mime_type, "fileName": file_name}
        payload.update(_partial_read_metadata(read_result))
    else:
        binary_result = await read_binary_chunk(valid_path, offset=offset, limit=limit)
        b64_data = b64.b64encode(binary_result.data).decode("ascii")
        mime_type, file_name = get_mime_and_filename(valid_path)
        payload = {"type": "base64", "base64": b64_data, "mimeType": mime_type, "fileName": file_name}
        payload.update(_binary_read_metadata(binary_result))
    return TextContent(
        type="text",
        text=json.dumps(payload),
    )


@mcp.tool(description="Create or overwrite a file. Completely replaces existing file content. Automatically creates parent directories if they don't exist.")
async def write_file(
    ctx: Context,
    path: str = Field(description="File path to write"),
    content: str = Field(description="File content"),
) -> TextContent:
    """Create or overwrite a file"""
    valid_path = await validate_path(path, allowed_directories)
    await write_file_content(valid_path, content)
    return TextContent(type="text", text=f"Successfully wrote to {path}")


@mcp.tool(
    description=(
        "Create or overwrite a file from base64-encoded bytes. "
        "Completely replaces existing file content and automatically creates parent directories."
    )
)
async def write_file_base64(
    ctx: Context,
    path: str = Field(description="File path to write"),
    content_base64: str = Field(description="Base64-encoded file content"),
) -> TextContent:
    """Create or overwrite a file from base64-encoded bytes."""
    valid_path = await validate_path(path, allowed_directories)
    await write_file_base64_content(valid_path, content_base64)
    return TextContent(type="text", text=f"Successfully wrote binary content to {path}")


@mcp.tool(description="Create directory. Automatically creates parent directories recursively. Silently succeeds if directory already exists.")
async def create_directory(
    ctx: Context,
    path: str = Field(description="Directory path to create"),
) -> TextContent:
    """Create directory"""
    valid_path = await validate_path(path, allowed_directories)
    Path(valid_path).mkdir(parents=True, exist_ok=True)
    return TextContent(type="text", text=f"Successfully created directory {path}")


@mcp.tool(description="List directory contents with file, directory, and symlink prefixes. Large directories are producer-bounded and return an explicit truncation marker.")
async def list_directory(
    ctx: Context,
    path: str = Field(description="Directory path to list"),
) -> TextContent:
    """List directory contents"""
    valid_path = await validate_path(path, allowed_directories)
    require_directory(valid_path)
    limits = FilesystemLimits.from_env()
    entries, truncated = await asyncio.to_thread(
        _list_directory_bounded, valid_path, limits.max_list_entries
    )
    if truncated:
        entries.append(
            truncation_notice(reason="list_entries", limit=limits.max_list_entries)
        )
    return TextContent(type="text", text="\n".join(entries))


@mcp.tool(description="Move or rename file. Can move files between directories or rename files within the same directory. Operation will fail if destination path already exists.")
async def move_file(
    ctx: Context,
    source: str = Field(description="Source path"),
    destination: str = Field(description="Destination path"),
) -> TextContent:
    """Move or rename file"""
    valid_source = await validate_path(source, allowed_directories)
    valid_dest = await validate_path(destination, allowed_directories)
    Path(valid_source).rename(valid_dest)
    return TextContent(type="text", text=f"Successfully moved {source} to {destination}")


@mcp.tool(description="List allowed directories. Shows all directories that the server currently allows access to. Useful for understanding the accessible scope.")
async def list_allowed_directories(
    ctx: Context,
) -> TextContent:
    """List allowed directories"""
    dirs = get_allowed_directories()
    text = "Allowed directories:\n" + "\n".join(dirs)
    return TextContent(type="text", text=text)

@mcp.tool(
    description=(
        "Edit file by line range. start_line/end_line are 1-based (inclusive). "
        "Replace lines [start_line, end_line] with new_content; empty new_content deletes those lines. "
        "dryRun previews the git-style diff without writing changes."
    )
)
async def edit_file(
    ctx: Context,
    path: str = Field(description="File path to edit"),
    start_line: int = Field(description="Start line number (1-based, inclusive)"),
    end_line: int = Field(description="End line number (1-based, inclusive)"),
    new_content: str = Field("", description="New content to replace these lines; empty to delete"),
    dryRun: bool = Field(False, description="Preview diff without applying changes"),
) -> TextContent:
    """Edit file by line range: replace lines [start_line, end_line] with new_content."""
    valid_path = await validate_path(path, allowed_directories)
    require_regular_file(valid_path)
    max_edit_bytes = FilesystemLimits.from_env().max_edit_bytes
    file_size = Path(valid_path).stat().st_size
    if file_size > max_edit_bytes:
        raise ValueError(
            f"File is too large for an atomic line edit: {file_size} bytes; "
            f"limit={max_edit_bytes}. Use a streaming command or split the file."
        )
    diff_text = await edit_file_by_line_range(
        valid_path,
        start_line=start_line,
        end_line=end_line,
        new_content=new_content,
        dry_run=dryRun,
    )
    return TextContent(type="text", text=diff_text)

@mcp.tool(
    description="Copy a regular server-side file into an allowed workspace path. source_path may be any readable path on this server; target_path must be allowed. Overwrites if target exists."
)
async def upload_file(
    ctx: Context,
    source_path: str = Field(description="Source file path readable by the filesystem server"),
    target_path: str = Field(description="Target path inside allowed directories; overwrites if exists"),
) -> TextContent:
    """Import a server-local file into the configured workspace authority."""
    source_resolved = resolve_and_require_file(source_path)
    require_regular_file(source_resolved)
    valid_target = await validate_path(target_path, allowed_directories)
    if Path(valid_target).exists() and Path(valid_target).is_dir():
        raise ValueError(f"Target path is a directory: {target_path}")
    await copy_file_binary(source_resolved, valid_target)
    return TextContent(type="text", text=f"Successfully uploaded {source_path} to {target_path}")


@mcp.tool(description="Download a bounded binary chunk by path. Returns base64, MIME metadata, and paging metadata when more bytes remain.")
async def download_file(
    ctx: Context,
    path: str = Field(description="Full path to file to download"),
    offset: Annotated[int, Field(description="Zero-based byte offset")] = 0,
    limit: Annotated[Optional[int], Field(description="Bytes to return; capped by server policy")] = None,
) -> TextContent:
    """Download file as base64 + metadata."""
    import base64 as b64
    valid_path = await validate_path(path, allowed_directories)
    if not Path(valid_path).exists():
        raise ValueError(f"Path does not exist: {path}")
    if not Path(valid_path).is_file():
        raise ValueError(f"Path is not a file: {path}")
    require_regular_file(valid_path)
    read_result = await read_binary_chunk(valid_path, offset=offset, limit=limit)
    b64_data = b64.b64encode(read_result.data).decode("ascii")
    mime_type, file_name = get_mime_and_filename(valid_path)
    payload = {"type": "base64", "base64": b64_data, "mimeType": mime_type, "fileName": file_name}
    payload.update(_binary_read_metadata(read_result))
    return TextContent(
        type="text",
        text=json.dumps(payload),
    )


@mcp.tool(
    description="Parse a readable server-side document to Markdown. output_path must be in allowed dirs and defaults to workspace/{stem}.md. file_type: pdf, txt, md, doc, docx, xlsx, xls, csv, ppt, pptx."
)
async def parse_file(
    ctx: Context,
    file_path: str = Field(description="Full path to file to parse"),
    file_type: Literal["pdf", "txt", "md", "doc", "docx", "xlsx", "xls", "csv", "ppt", "pptx"] = Field(
        description="File type"
    ),
    output_path: Optional[str] = Field(None, description="Output path for Markdown; default workspace / {stem}.md"),
) -> TextContent:
    """Parse document to Markdown and write to output_path."""
    source_resolved = resolve_and_require_file(file_path)
    require_regular_file(source_resolved)
    max_parse_bytes = FilesystemLimits.from_env().max_parse_bytes
    source_size = Path(source_resolved).stat().st_size
    if source_size > max_parse_bytes:
        raise ValueError(
            f"Document is too large to parse safely: {source_size} bytes; "
            f"limit={max_parse_bytes}"
        )
    if not allowed_directories:
        raise ValueError("No allowed directories configured")
    if output_path is None or not output_path.strip():
        default_dir = allowed_directories[0]
        default_name = f"{Path(file_path).stem}.md"
        output_path = str(Path(default_dir) / default_name)
    output_valid = await validate_path(output_path, allowed_directories)
    if Path(output_valid).exists() and Path(output_valid).is_dir():
        raise ValueError(f"Output path is a directory: {output_path}")
    if not parse_verify_file_type(Path(source_resolved), file_type):
        raise ValueError(f"File type does not match content; expected: {file_type}")
    try:
        result_path = await parse_file_to_path(source_resolved, output_valid, file_type)
        return TextContent(
            type="text",
            text=json.dumps({"success": True, "message": "Document parsed successfully", "output_path": result_path}),
        )
    except NotImplementedError as e:
        # Raising makes MCP's isError flag truthful; a nested success=false in a
        # successful TextContent was previously lowered to ActionResult.success.
        raise RuntimeError(str(e)) from e
    except Exception as e:
        raise RuntimeError(f"Document parsing failed: {e}") from e


@mcp.tool(
    description=(
        "Search file or directory for lines matching a pattern (regex). "
        "path can be a file or directory; if directory, recurses. "
        "Returns one line per match: absolute_path:line_number:line_content. "
        "Optional max_matches / max_per_file lower the server's finite safety caps. "
        "Searches have byte/file/output/deadline budgets and report truncation explicitly. "
        "Use before/after to include context lines around each match."
    )
)
async def search_content(
    ctx: Context,
    path: str = Field(description="File or directory path to search"),
    pattern: str = Field(description="Regex pattern to match in line content (e.g. keyword or full regex)"),
    max_matches: Optional[int] = Field(None, description="Maximum total matching lines; default uses server safety cap"),
    max_per_file: Optional[int] = Field(None, description="Maximum matching lines per file; default uses server safety cap"),
    before: int = Field(0, description="Number of context lines to include before each match"),
    after: int = Field(0, description="Number of context lines to include after each match"),
) -> TextContent:
    """Search content in file(s) by regex; path may be file or directory."""
    valid_path = await validate_path(path, allowed_directories)
    if not Path(valid_path).exists():
        raise ValueError(f"Path does not exist: {path}")
    if not (Path(valid_path).is_file() or Path(valid_path).is_dir()):
        raise ValueError(f"Path must be a regular file or directory: {path}")
    text = await search_content_impl(
        valid_path,
        pattern=pattern,
        max_matches=max_matches,
        max_per_file=max_per_file,
        before=before,
        after=after,
    )
    return TextContent(type="text", text=text)


# ==================== Additional MCP tools ====================

@mcp.tool(description="Read a bounded image/audio/blob chunk as base64. Partial results include byte paging metadata.")
async def read_media_file(
    ctx: Context,
    path: str = Field(description="Media file path"),
    offset: Annotated[int, Field(description="Zero-based byte offset")] = 0,
    limit: Annotated[Optional[int], Field(description="Bytes to return; capped by server policy")] = None,
) -> TextContent:
    """Read image or audio file as base64"""
    import base64 as b64

    valid_path = await validate_path(path, allowed_directories)
    require_regular_file(valid_path)
    read_result = await read_binary_chunk(valid_path, offset=offset, limit=limit)
    mime_type, _ = get_mime_and_filename(valid_path)
    if mime_type.startswith("image/"):
        media_type = "image"
    elif mime_type.startswith("audio/"):
        media_type = "audio"
    else:
        media_type = "blob"

    result = {
        "type": media_type,
        "data": b64.b64encode(read_result.data).decode("ascii"),
        "mimeType": mime_type
    }
    result.update(_binary_read_metadata(read_result))
    return TextContent(type="text", text=json.dumps(result))


#@mcp.tool(description="Read multiple files simultaneously. More efficient than reading files one by one. Individual file read failures won't stop the entire operation.")
async def read_multiple_files(
    ctx: Context,
    paths: list[str] = Field(description="Array of file paths to read"),
) -> TextContent:
    """Read multiple files simultaneously"""
    results = []
    for file_path in paths:
        try:
            valid_path = await validate_path(file_path, allowed_directories)
            content = await read_file_content(valid_path)
            results.append(f"{file_path}:\n{content}\n")
        except Exception as e:
            results.append(f"{file_path}: Error - {str(e)}")

    return TextContent(type="text", text="\n---\n".join(results))


#@mcp.tool(description="List directory contents with file sizes. Shows file sizes, supports sorting by name or size. Displays statistics including total file count, total directory count, and combined size.")
async def list_directory_with_sizes(
    ctx: Context,
    path: str = Field(description="Directory path to list"),
    sortBy: str = Field("name", description="Sort by 'name' or 'size'"),
) -> TextContent:
    """List directory with file sizes"""
    valid_path = await validate_path(path, allowed_directories)
    entries = []

    for entry in Path(valid_path).iterdir():
        try:
            stat = entry.stat()
            size = stat.st_size if entry.is_file() else 0
            entries.append({
                "name": entry.name,
                "isDirectory": entry.is_dir(),
                "size": size,
            })
        except OSError:
            entries.append({
                "name": entry.name,
                "isDirectory": entry.is_dir(),
                "size": 0,
            })

    if sortBy == "size":
        entries.sort(key=lambda x: x["size"], reverse=True)
    else:
        entries.sort(key=lambda x: x["name"])

    formatted = []
    total_files = sum(1 for e in entries if not e["isDirectory"])
    total_dirs = sum(1 for e in entries if e["isDirectory"])
    total_size = sum(e["size"] for e in entries if not e["isDirectory"])

    for entry in entries:
        prefix = "[DIR]" if entry["isDirectory"] else "[FILE]"
        size_str = "" if entry["isDirectory"] else format_size(entry["size"]).rjust(10)
        formatted.append(f"{prefix} {entry['name']:<30} {size_str}")

    formatted.append("")
    formatted.append(f"Total: {total_files} files, {total_dirs} directories")
    formatted.append(f"Combined size: {format_size(total_size)}")

    return TextContent(type="text", text="\n".join(formatted))


#@mcp.tool(description="Get directory tree as JSON structure. Returns recursive directory tree in JSON format. Supports exclude patterns (glob format). Each node contains name, type, and children array.")
async def directory_tree(
    ctx: Context,
    path: str = Field(description="Directory path"),
    excludePatterns: list[str] = Field(default_factory=list, description="Exclude patterns"),
) -> TextContent:
    """Get directory tree as JSON"""
    valid_path = await validate_path(path, allowed_directories)

    def should_exclude(relative_path: str) -> bool:
        for pattern in excludePatterns:
            if fnmatch(relative_path, pattern) or fnmatch(relative_path, f"**/{pattern}"):
                return True
        return False

    def build_tree(current: Path, root: Path) -> list[dict]:
        result = []
        try:
            for entry in current.iterdir():
                relative = str((current / entry.name).relative_to(root))
                if should_exclude(relative):
                    continue

                entry_data = {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                }

                if entry.is_dir():
                    entry_data["children"] = build_tree(current / entry.name, root)

                result.append(entry_data)
        except (PermissionError, OSError):
            pass
        return result

    tree_data = build_tree(Path(valid_path), Path(valid_path))
    return TextContent(type="text", text=json.dumps(tree_data, indent=2))


@mcp.tool(description="Search for paths matching a glob pattern. Recurses without following symlinks and applies finite traversal/output/deadline budgets. Truncation is reported explicitly.")
async def search_files(
    ctx: Context,
    path: str = Field(description="Search root path"),
    pattern: str = Field(description="Search pattern (glob)"),
    excludePatterns: list[str] = Field(default_factory=list, description="Exclude patterns"),
) -> TextContent:
    """Search for files matching pattern"""
    valid_path = await validate_path(path, allowed_directories)
    require_directory(valid_path)
    limits = FilesystemLimits.from_env()
    result = await asyncio.to_thread(
        search_files_bounded,
        valid_path,
        pattern=pattern,
        exclude_patterns=excludePatterns,
        limits=limits,
    )
    text = "\n".join(result.paths) if result.paths else "No matches found"
    if not result.complete:
        reason = result.truncation_reason or "unknown"
        text += "\n" + truncation_notice(
            reason=reason,
            limit={
                "search_files": limits.max_search_files,
                "search_output_bytes": limits.max_search_output_bytes,
                "search_depth": limits.max_search_depth,
                "timeout": limits.search_timeout_seconds,
            }.get(reason, 0),
        )
    return TextContent(type="text", text=text)


#@mcp.tool(description="Get file metadata. Returns file size, creation time, modification time, access time, file type (file/directory), and permissions information.")
async def get_file_info(
    ctx: Context,
    path: str = Field(description="File or directory path"),
) -> TextContent:
    """Get file metadata"""
    valid_path = await validate_path(path, allowed_directories)
    info = await get_file_stats(valid_path)
    lines = [f"{key}: {value}" for key, value in info.items()]
    return TextContent(type="text", text="\n".join(lines))


if __name__ == "__main__":
    import asyncio

    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    # Allowed directories: read from AWORLD_WORKSPACE (comma-separated); fall back to defaults if unset
    home_dir = Path.home()
    DEFAULT_WORKSPACES = [
        str(home_dir / "workspace"),
        str(home_dir / "aworld_workspace"),
        str("/tmp")
    ]
    env_workspace = os.environ.get("AWORLD_WORKSPACE", "").strip()
    if env_workspace:
        args = [p.strip() for p in env_workspace.split(",") if p.strip()]
    else:
        args = DEFAULT_WORKSPACES

    asyncio.run(set_allowed_directories(args))

    # Print allowed directories for visibility
    allowed_dirs = get_allowed_directories()
    logging.info("Allowed directories:")
    for i, dir_path in enumerate(allowed_dirs, 1):
        logging.info(f"  {i}. {dir_path}")
    
    # Run the server: default streamable-http (compat with start_tool_servers.sh); use stdio when --stdio or MCP_TRANSPORT=stdio
    use_stdio = "--stdio" in sys.argv or os.environ.get("MCP_TRANSPORT", "").strip().lower() == "stdio"
    try:
        if use_stdio:
            mcp.run(transport="stdio")
        else:
            mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logging.info("Filesystem MCP server stopped")
