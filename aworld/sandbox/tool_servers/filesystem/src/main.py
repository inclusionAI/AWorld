"""Filesystem MCP Server - powered by FastMCP"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Literal, Optional
from fnmatch import fnmatch

from mcp.server import FastMCP
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field

from utils.path_utils import validate_path, normalize_path, resolve_and_require_file
from utils.document_processor import (
    parse_to_path as parse_file_to_path,
    verify_file_type as parse_verify_file_type,
)
from utils.file_ops import (
    read_file as read_file_content,
    write_file as write_file_content,
    head_file,
    tail_file,
    read_file_lines,
    read_file_binary,
    is_text_file,
    get_mime_and_filename,
    apply_edits,
    apply_edits_range,
    copy_file_binary,
    read_media_file as read_media_file_content,
    get_file_stats,
    format_size,
    edit_file_by_line_range,
)

# List of allowed directories for all tools
allowed_directories: list[str] = []


async def set_allowed_directories(dirs: list[str]) -> None:
    """Configure the directories that filesystem tools are allowed to access."""
    global allowed_directories
    allowed_directories = [normalize_path(d) for d in dirs]


def get_allowed_directories() -> list[str]:
    """Return a copy of the currently allowed directories."""
    return allowed_directories.copy()


# Initialize FastMCP server
mcp = FastMCP(
    "filesystem-server",
    log_level="DEBUG",
    port=8084,
    instructions="Filesystem MCP Server for file operations"
)


# ==================== Enabled MCP tools ====================

@mcp.tool(
    description="Read file content. Use output='text' for text (supports head/tail); use output='base64' for binary. "
    "head: first N lines; tail: last N lines; both: lines head to tail (1-based inclusive). "
    "Returns JSON: {\"type\":\"text\",\"content\":\"...\"} or {\"type\":\"base64\",\"base64\":\"...\",\"mimeType\":\"...\",\"fileName\":\"...\"}."
)
async def read_file(
    ctx: Context,
    path: str = Field(description="File path to read"),
    head: Optional[int] = Field(None, description="First N lines, or start line when used with tail"),
    tail: Optional[int] = Field(None, description="Last N lines, or end line when used with head"),
    output: str = Field("text", description="Output format: 'text' or 'base64'"),
) -> TextContent:
    """Read file as text or base64; head/tail apply only when file is text (content-based detection)."""
    import base64 as b64
    valid_path = await validate_path(path, allowed_directories)
    if output not in ("text", "base64"):
        raise ValueError("output must be 'text' or 'base64'")

    if output == "text":
        if not await is_text_file(valid_path):
            raise ValueError("File is not valid UTF-8 text; use output='base64' for binary files")
        if tail and head:
            if head > tail:
                raise ValueError("head must be <= tail when both are specified")
            content = await read_file_lines(valid_path, head, tail)
        elif tail:
            content = await tail_file(valid_path, tail)
        elif head:
            content = await head_file(valid_path, head)
        else:
            content = await read_file_content(valid_path)
        return TextContent(type="text", text=json.dumps({"type": "text", "content": content}))

    # output == "base64"
    is_text = await is_text_file(valid_path)
    if is_text and (head or tail):
        if head and tail:
            if head > tail:
                raise ValueError("head must be <= tail when both are specified")
            text_content = await read_file_lines(valid_path, head, tail)
        elif head:
            text_content = await head_file(valid_path, head)
        else:
            text_content = await tail_file(valid_path, tail)
        b64_data = b64.b64encode(text_content.encode("utf-8")).decode("ascii")
        mime_type = "text/plain; charset=utf-8"
        file_name = Path(valid_path).name
    else:
        data = await read_file_binary(valid_path)
        b64_data = b64.b64encode(data).decode("ascii")
        mime_type, file_name = get_mime_and_filename(valid_path)
    return TextContent(
        type="text",
        text=json.dumps({"type": "base64", "base64": b64_data, "mimeType": mime_type, "fileName": file_name}),
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


@mcp.tool(description="Create directory. Automatically creates parent directories recursively. Silently succeeds if directory already exists.")
async def create_directory(
    ctx: Context,
    path: str = Field(description="Directory path to create"),
) -> TextContent:
    """Create directory"""
    valid_path = await validate_path(path, allowed_directories)
    Path(valid_path).mkdir(parents=True, exist_ok=True)
    return TextContent(type="text", text=f"Successfully created directory {path}")


@mcp.tool(description="List directory contents. Shows files and directories with [FILE] and [DIR] prefixes to distinguish types.")
async def list_directory(
    ctx: Context,
    path: str = Field(description="Directory path to list"),
) -> TextContent:
    """List directory contents"""
    valid_path = await validate_path(path, allowed_directories)
    entries = []
    for entry in Path(valid_path).iterdir():
        prefix = "[DIR]" if entry.is_dir() else "[FILE]"
        entries.append(f"{prefix} {entry.name}")
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
    diff_text = await edit_file_by_line_range(
        valid_path,
        start_line=start_line,
        end_line=end_line,
        new_content=new_content,
        dry_run=dryRun,
    )
    return TextContent(type="text", text=diff_text)

@mcp.tool(
    description="Copy file from source_path to target_path (server-side). source_path can be any readable path; target_path must be inside allowed directories. Overwrites if target exists."
)
async def upload_file(
    ctx: Context,
    source_path: str = Field(description="Source file path (any readable path on server)"),
    target_path: str = Field(description="Target path inside allowed directories; overwrites if exists"),
) -> TextContent:
    """Copy file from source to target (target must be in allowed directories)."""
    source_resolved = resolve_and_require_file(source_path)
    valid_target = await validate_path(target_path, allowed_directories)
    if Path(valid_target).exists() and Path(valid_target).is_dir():
        raise ValueError(f"Target path is a directory: {target_path}")
    await copy_file_binary(source_resolved, valid_target)
    return TextContent(type="text", text=f"Successfully uploaded {source_path} to {target_path}")


@mcp.tool(description="Download file by path. Returns JSON with base64 content, mimeType, and fileName.")
async def download_file(
    ctx: Context,
    path: str = Field(description="Full path to file to download"),
) -> TextContent:
    """Download file as base64 + metadata."""
    import base64 as b64
    valid_path = await validate_path(path, allowed_directories)
    if not Path(valid_path).exists():
        raise ValueError(f"Path does not exist: {path}")
    if not Path(valid_path).is_file():
        raise ValueError(f"Path is not a file: {path}")
    data = await read_file_binary(valid_path)
    b64_data = b64.b64encode(data).decode("ascii")
    mime_type, file_name = get_mime_and_filename(valid_path)
    return TextContent(
        type="text",
        text=json.dumps({"type": "base64", "base64": b64_data, "mimeType": mime_type, "fileName": file_name}),
    )


@mcp.tool(
    description="Parse document to Markdown. file_path: any readable path. output_path: optional, must be in allowed dirs; default is workspace / {stem}.md. file_type: pdf, txt, md, doc, docx, xlsx, xls, csv, ppt, pptx."
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
        return TextContent(
            type="text",
            text=json.dumps({"success": False, "message": str(e)}),
        )
    except Exception as e:
        return TextContent(
            type="text",
            text=json.dumps({"success": False, "message": str(e)}),
        )


# ==================== Disabled MCP tools (not exposed) ====================

#@mcp.tool(description="Read image or audio file as base64 encoded data. Returns base64 data, MIME type, and media type (image/audio/blob).")
async def read_media_file(
    ctx: Context,
    path: str = Field(description="Media file path"),
) -> TextContent:
    """Read image or audio file as base64"""
    valid_path = await validate_path(path, allowed_directories)
    data, mime_type, media_type = await read_media_file_content(valid_path)

    result = {
        "type": media_type,
        "data": data,
        "mimeType": mime_type
    }
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


#@mcp.tool(description="Search for files matching pattern. Uses glob pattern matching. Recursively searches subdirectories. Supports exclude patterns. Returns list of full paths to matching files.")
async def search_files(
    ctx: Context,
    path: str = Field(description="Search root path"),
    pattern: str = Field(description="Search pattern (glob)"),
    excludePatterns: list[str] = Field(default_factory=list, description="Exclude patterns"),
) -> TextContent:
    """Search for files matching pattern"""
    valid_path = await validate_path(path, allowed_directories)
    results = []
    root = Path(valid_path)

    def should_exclude(relative: str) -> bool:
        for pattern in excludePatterns:
            if fnmatch(relative, pattern) or fnmatch(relative, f"**/{pattern}"):
                return True
        return False

    async def search(current: Path):
        try:
            for entry in current.iterdir():
                full_path = current / entry.name
                try:
                    await validate_path(str(full_path), allowed_directories)
                    relative = str(full_path.relative_to(root))
                    if should_exclude(relative):
                        continue
                    if fnmatch(relative, pattern) or fnmatch(entry.name, pattern):
                        results.append(str(full_path))
                    if entry.is_dir():
                        await search(full_path)
                except (ValueError, PermissionError):
                    continue
        except (PermissionError, OSError):
            pass

    await search(root)
    text = "\n".join(results) if results else "No matches found"
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
    
    # Run the server
    #mcp.run(transport="stdio")
    mcp.run(transport="streamable-http")
