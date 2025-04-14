"""
Filesystem MCP Server

This module provides MCP server functionality for filesystem operations.
It offers a comprehensive set of tools for interacting with the local filesystem,
including file reading, writing, searching, and manipulation.

Key features:
- File and directory information retrieval
- File content reading with various options
- File writing and modification
- Directory listing and traversal
- File searching with pattern matching
- File compression and extraction
- File and directory operations (copy, move, delete)

All operations include proper validation, error handling, and security checks
to ensure safe filesystem access.

Main functions:
- mcpreadfile: Reads file contents with various options
- mcpwritefile: Writes content to files
- mcplistdir: Lists directory contents
- mcpsearchfiles: Searches for files matching patterns
- mcpgetfileinfo: Gets detailed file information
- mcpcompressfiles: Compresses files into archives
"""

import glob
import os
import shutil
import stat
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server
from aworld.utils import import_package

# Import magic package, install if not available
import_package("magic", install_name="python-magic")


class FileInfo(BaseModel):
    """Model representing file information"""

    name: str
    path: str
    size: int
    is_dir: bool
    created: float
    modified: float
    accessed: float
    permissions: str
    owner: str = ""
    group: str = ""
    mime_type: str = ""


class DirectoryContents(BaseModel):
    """Model representing directory contents"""

    path: str
    files: List[FileInfo]
    count: int
    total_size: int
    error: Optional[str] = None


class FileOperation(BaseModel):
    """Model representing file operation result"""

    operation: str
    path: str
    success: bool
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


def get_file_info(path: str) -> FileInfo:
    """
    Get detailed information about a file or directory.

    Args:
        path: Path to the file or directory

    Returns:
        FileInfo object with file details
    """
    try:
        path = os.path.abspath(path)
        stat_info = os.stat(path)

        # Get file permissions as string (e.g., "rwxr-xr-x")
        permissions = ""
        mode = stat_info.st_mode
        for who in ["USR", "GRP", "OTH"]:
            for what in ["R", "W", "X"]:
                permissions += (
                    what.lower() if mode & getattr(stat, "S_I" + what + who) else "-"
                )

        # Try to determine mime type
        mime_type = ""
        try:
            import magic

            mime_type = magic.from_file(path, mime=True)
        except ImportError:
            # If python-magic is not installed, use a simple approach
            if os.path.isdir(path):
                mime_type = "directory"
            else:
                ext = os.path.splitext(path)[1].lower()
                mime_map = {
                    ".txt": "text/plain",
                    ".html": "text/html",
                    ".htm": "text/html",
                    ".pdf": "application/pdf",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".json": "application/json",
                    ".xml": "application/xml",
                    ".zip": "application/zip",
                    ".py": "text/x-python",
                    ".js": "application/javascript",
                    ".css": "text/css",
                }
                mime_type = mime_map.get(ext, "application/octet-stream")

        # Get owner and group names
        owner = ""
        group = ""
        try:
            import grp
            import pwd

            owner = pwd.getpwuid(stat_info.st_uid).pw_name
            group = grp.getgrgid(stat_info.st_gid).gr_name
        except (ImportError, KeyError):
            # If pwd/grp modules are not available or user/group not found
            owner = str(stat_info.st_uid)
            group = str(stat_info.st_gid)

        return FileInfo(
            name=os.path.basename(path),
            path=path,
            size=stat_info.st_size,
            is_dir=os.path.isdir(path),
            created=stat_info.st_ctime,
            modified=stat_info.st_mtime,
            accessed=stat_info.st_atime,
            permissions=permissions,
            owner=owner,
            group=group,
            mime_type=mime_type,
        )
    except Exception as e:
        logger.error(f"Error getting file info for {path}: {str(e)}")
        raise


def mcplistdir(
    path: str = Field(..., description="Directory path to list"),
    include_hidden: bool = Field(False, description="Whether to include hidden files"),
    recursive: bool = Field(
        False, description="Whether to list subdirectories recursively"
    ),
    max_depth: int = Field(
        3, description="Maximum recursion depth when recursive is True"
    ),
    pattern: Optional[str] = Field(
        None, description="File pattern to match (e.g., '*.txt')"
    ),
    sort_by: str = Field(
        "name", description="Sort results by: name, size, modified, created"
    ),
    sort_order: str = Field("asc", description="Sort order: asc or desc"),
) -> str:
    """
    List contents of a directory with detailed information.

    Args:
        path: Directory path to list
        include_hidden: Whether to include hidden files
        recursive: Whether to list subdirectories recursively
        max_depth: Maximum recursion depth when recursive is True
        pattern: File pattern to match (e.g., '*.txt')
        sort_by: Sort results by: name, size, modified, created
        sort_order: Sort order: asc or desc

    Returns:
        JSON string with directory contents
    """
    try:
        path = os.path.abspath(path)

        if not os.path.exists(path):
            return DirectoryContents(
                path=path,
                files=[],
                count=0,
                total_size=0,
                error=f"Path does not exist: {path}",
            ).model_dump_json()

        if not os.path.isdir(path):
            return DirectoryContents(
                path=path,
                files=[],
                count=0,
                total_size=0,
                error=f"Path is not a directory: {path}",
            ).model_dump_json()

        files = []
        total_size = 0

        def process_directory(dir_path, current_depth=0):
            nonlocal files, total_size

            if recursive and current_depth > max_depth:
                return

            try:
                entries = os.listdir(dir_path)

                for entry in entries:
                    entry_path = os.path.join(dir_path, entry)

                    # Skip hidden files if not included
                    if not include_hidden and entry.startswith("."):
                        continue

                    # Apply pattern filter if specified
                    if pattern and not glob.fnmatch.fnmatch(entry, pattern):
                        continue

                    try:
                        file_info = get_file_info(entry_path)
                        files.append(file_info)
                        total_size += file_info.size

                        # Process subdirectories if recursive
                        if recursive and file_info.is_dir:
                            process_directory(entry_path, current_depth + 1)
                    except Exception as e:
                        logger.warning(f"Error processing {entry_path}: {str(e)}")
            except Exception as e:
                logger.warning(f"Error listing directory {dir_path}: {str(e)}")

        # Process the main directory
        process_directory(path)

        # Sort the results
        sort_key_map = {
            "name": lambda x: x.name.lower(),
            "size": lambda x: x.size,
            "modified": lambda x: x.modified,
            "created": lambda x: x.created,
        }

        sort_key = sort_key_map.get(sort_by.lower(), sort_key_map["name"])
        reverse = sort_order.lower() == "desc"

        files.sort(key=sort_key, reverse=reverse)

        return DirectoryContents(
            path=path, files=files, count=len(files), total_size=total_size
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error listing directory {path}: {str(e)}")
        return DirectoryContents(
            path=path, files=[], count=0, total_size=0, error=str(e)
        ).model_dump_json()


def mcpreadfile(
    path: str = Field(..., description="Path to the file to read"),
    encoding: str = Field("utf-8", description="File encoding"),
    max_size: int = Field(
        10 * 1024 * 1024, description="Maximum file size to read (in bytes)"
    ),
    start_line: Optional[int] = Field(
        None, description="Start reading from this line (1-based)"
    ),
    end_line: Optional[int] = Field(
        None, description="End reading at this line (inclusive)"
    ),
    include_line_numbers: bool = Field(
        False, description="Include line numbers in output"
    ),
) -> str:
    """
    Read contents of a file.

    Args:
        path: Path to the file to read
        encoding: File encoding
        max_size: Maximum file size to read (in bytes)
        start_line: Start reading from this line (1-based)
        end_line: End reading at this line (inclusive)
        include_line_numbers: Include line numbers in output

    Returns:
        JSON string with file contents
    """
    try:
        path = os.path.abspath(path)

        if not os.path.exists(path):
            return FileOperation(
                operation="read",
                path=path,
                success=False,
                error=f"File does not exist: {path}",
            ).model_dump_json()

        if not os.path.isfile(path):
            return FileOperation(
                operation="read",
                path=path,
                success=False,
                error=f"Path is not a file: {path}",
            ).model_dump_json()

        # Check file size
        file_size = os.path.getsize(path)
        if file_size > max_size:
            return FileOperation(
                operation="read",
                path=path,
                success=False,
                error=f"File size ({file_size} bytes) exceeds maximum allowed size ({max_size} bytes)",
            ).model_dump_json()

        # Read the file
        content = ""
        line_count = 0

        if start_line is not None or end_line is not None:
            # Read specific lines
            lines = []
            with open(path, "r", encoding=encoding) as f:
                for i, line in enumerate(f, 1):
                    if start_line is not None and i < start_line:
                        continue
                    if end_line is not None and i > end_line:
                        break

                    if include_line_numbers:
                        lines.append(f"{i}: {line}")
                    else:
                        lines.append(line)
                    line_count = i

            content = "".join(lines)
        else:
            # Read the entire file
            with open(path, "r", encoding=encoding) as f:
                content = f.read()
                line_count = content.count("\n") + 1

        # Get file info
        file_info = get_file_info(path)

        return FileOperation(
            operation="read",
            path=path,
            success=True,
            details={
                "content": content,
                "size": file_size,
                "encoding": encoding,
                "line_count": line_count,
                "file_info": file_info.model_dump(),
            },
        ).model_dump_json()

    except UnicodeDecodeError:
        return FileOperation(
            operation="read",
            path=path,
            success=False,
            error=f"File is not valid {encoding} encoded text",
        ).model_dump_json()
    except Exception as e:
        logger.error(f"Error reading file {path}: {str(e)}")
        return FileOperation(
            operation="read", path=path, success=False, error=str(e)
        ).model_dump_json()


def mcpwritefile(
    path: str = Field(..., description="Path to the file to write"),
    content: str = Field(..., description="Content to write to the file"),
    encoding: str = Field("utf-8", description="File encoding"),
    append: bool = Field(False, description="Append to file instead of overwriting"),
    create_dirs: bool = Field(
        True, description="Create parent directories if they don't exist"
    ),
) -> str:
    """
    Write content to a file.

    Args:
        path: Path to the file to write
        content: Content to write to the file
        encoding: File encoding
        append: Append to file instead of overwriting
        create_dirs: Create parent directories if they don't exist

    Returns:
        JSON string with operation result
    """
    try:
        path = os.path.abspath(path)

        # Create parent directories if needed
        if create_dirs:
            os.makedirs(os.path.dirname(path), exist_ok=True)

        # Write to the file
        mode = "a" if append else "w"
        with open(path, mode, encoding=encoding) as f:
            f.write(content)

        # Get file info
        file_info = get_file_info(path)

        return FileOperation(
            operation="write",
            path=path,
            success=True,
            details={
                "size": file_info.size,
                "append": append,
                "file_info": file_info.model_dump(),
            },
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error writing to file {path}: {str(e)}")
        return FileOperation(
            operation="write", path=path, success=False, error=str(e)
        ).model_dump_json()


def mcpcreatedirectory(
    path: str = Field(..., description="Path to the directory to create"),
    parents: bool = Field(
        True, description="Create parent directories if they don't exist"
    ),
    exist_ok: bool = Field(True, description="Don't error if directory already exists"),
) -> str:
    """
    Create a directory.

    Args:
        path: Path to the directory to create
        parents: Create parent directories if they don't exist
        exist_ok: Don't error if directory already exists

    Returns:
        JSON string with operation result
    """
    try:
        path = os.path.abspath(path)

        # Create the directory
        os.makedirs(path, exist_ok=exist_ok)

        # Get directory info
        dir_info = get_file_info(path)

        return FileOperation(
            operation="create_directory",
            path=path,
            success=True,
            details={"directory_info": dir_info.model_dump()},
        ).model_dump_json()

    except FileExistsError:
        return FileOperation(
            operation="create_directory",
            path=path,
            success=False,
            error=f"Directory already exists: {path}",
        ).model_dump_json()
    except Exception as e:
        logger.error(f"Error creating directory {path}: {str(e)}")
        return FileOperation(
            operation="create_directory", path=path, success=False, error=str(e)
        ).model_dump_json()


def mcpdeletepath(
    path: str = Field(..., description="Path to the file or directory to delete"),
    recursive: bool = Field(False, description="Recursively delete directories"),
    force: bool = Field(False, description="Force deletion (ignore errors)"),
) -> str:
    """
    Delete a file or directory.

    Args:
        path: Path to the file or directory to delete
        recursive: Recursively delete directories
        force: Force deletion (ignore errors)

    Returns:
        JSON string with operation result
    """
    try:
        path = os.path.abspath(path)

        if not os.path.exists(path):
            return FileOperation(
                operation="delete",
                path=path,
                success=False,
                error=f"Path does not exist: {path}",
            ).model_dump_json()

        # Store file info before deletion
        try:
            file_info = get_file_info(path)
            was_directory = file_info.is_dir
        except:
            was_directory = os.path.isdir(path)
            file_info = None

        # Delete the path
        if os.path.isdir(path):
            if recursive:
                shutil.rmtree(path, ignore_errors=force)
            else:
                os.rmdir(path)
        else:
            os.remove(path)

        return FileOperation(
            operation="delete",
            path=path,
            success=True,
            details={
                "was_directory": was_directory,
                "file_info": file_info.model_dump() if file_info else None,
            },
        ).model_dump_json()

    except OSError as e:
        if os.path.isdir(path) and not recursive:
            return FileOperation(
                operation="delete",
                path=path,
                success=False,
                error=f"Cannot delete directory with contents. Use recursive=True to delete recursively.",
            ).model_dump_json()
        else:
            return FileOperation(
                operation="delete", path=path, success=False, error=str(e)
            ).model_dump_json()
    except Exception as e:
        logger.error(f"Error deleting {path}: {str(e)}")
        return FileOperation(
            operation="delete", path=path, success=False, error=str(e)
        ).model_dump_json()


def mcpcopypath(
    source: str = Field(..., description="Source path to copy from"),
    destination: str = Field(..., description="Destination path to copy to"),
    recursive: bool = Field(True, description="Recursively copy directories"),
    overwrite: bool = Field(False, description="Overwrite destination if it exists"),
) -> str:
    """
    Copy a file or directory.

    Args:
        source: Source path to copy from
        destination: Destination path to copy to
        recursive: Recursively copy directories
        overwrite: Overwrite destination if it exists

    Returns:
        JSON string with operation result
    """
    try:
        source = os.path.abspath(source)
        destination = os.path.abspath(destination)

        if not os.path.exists(source):
            return FileOperation(
                operation="copy",
                path=source,
                success=False,
                error=f"Source path does not exist: {source}",
            ).model_dump_json()

        # Check if destination exists
        if os.path.exists(destination):
            if not overwrite:
                return FileOperation(
                    operation="copy",
                    path=source,
                    success=False,
                    error=f"Destination already exists: {destination}. Use overwrite=True to overwrite.",
                ).model_dump_json()

            # Remove destination if overwrite is True
            if os.path.isdir(destination):
                if os.path.isdir(source):
                    shutil.rmtree(destination)
                else:
                    os.remove(destination)
            else:
                os.remove(destination)

        # Copy the path
        if os.path.isdir(source):
            if recursive:
                shutil.copytree(source, destination)
            else:
                os.makedirs(destination, exist_ok=True)
                for item in os.listdir(source):
                    s = os.path.join(source, item)
                    d = os.path.join(destination, item)
                    if os.path.isfile(s):
                        shutil.copy2(s, d)
        else:
            # Create parent directories if they don't exist
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(source, destination)

        # Get file info for source and destination
        source_info = get_file_info(source)
        dest_info = get_file_info(destination)

        return FileOperation(
            operation="copy",
            path=source,
            success=True,
            details={
                "source": source,
                "destination": destination,
                "source_info": source_info.model_dump(),
                "destination_info": dest_info.model_dump(),
            },
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error copying {source} to {destination}: {str(e)}")
        return FileOperation(
            operation="copy", path=source, success=False, error=str(e)
        ).model_dump_json()


def mcpmovepath(
    source: str = Field(..., description="Source path to move from"),
    destination: str = Field(..., description="Destination path to move to"),
    overwrite: bool = Field(False, description="Overwrite destination if it exists"),
) -> str:
    """
    Move a file or directory.

    Args:
        source: Source path to move from
        destination: Destination path to move to
        overwrite: Overwrite destination if it exists

    Returns:
        JSON string with operation result
    """
    try:
        source = os.path.abspath(source)
        destination = os.path.abspath(destination)

        if not os.path.exists(source):
            return FileOperation(
                operation="move",
                path=source,
                success=False,
                error=f"Source path does not exist: {source}",
            ).model_dump_json()

        # Check if destination exists
        if os.path.exists(destination):
            if not overwrite:
                return FileOperation(
                    operation="move",
                    path=source,
                    success=False,
                    error=f"Destination already exists: {destination}. Use overwrite=True to overwrite.",
                ).model_dump_json()

            # Remove destination if overwrite is True
            if os.path.isdir(destination):
                shutil.rmtree(destination)
            else:
                os.remove(destination)

        # Store source info before moving
        source_info = get_file_info(source)

        # Create parent directories if they don't exist
        os.makedirs(os.path.dirname(destination), exist_ok=True)

        # Move the path
        shutil.move(source, destination)

        # Get destination info
        dest_info = get_file_info(destination)

        return FileOperation(
            operation="move",
            path=source,
            success=True,
            details={
                "source": source,
                "destination": destination,
                "source_info": source_info.model_dump(),
                "destination_info": dest_info.model_dump(),
            },
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error moving {source} to {destination}: {str(e)}")
        return FileOperation(
            operation="move", path=source, success=False, error=str(e)
        ).model_dump_json()


def mcpsearchfiles(
    path: str = Field(..., description="Base directory to search in"),
    pattern: str = Field("*", description="File pattern to match (e.g., '*.txt')"),
    recursive: bool = Field(True, description="Search subdirectories recursively"),
    max_depth: int = Field(10, description="Maximum recursion depth"),
    include_hidden: bool = Field(
        False, description="Include hidden files and directories"
    ),
    max_results: int = Field(1000, description="Maximum number of results to return"),
    content_match: Optional[str] = Field(
        None, description="Search for files containing this text"
    ),
    case_sensitive: bool = Field(False, description="Case-sensitive content matching"),
    sort_by: str = Field(
        "path", description="Sort results by: path, name, size, modified"
    ),
    sort_order: str = Field("asc", description="Sort order: asc or desc"),
) -> str:
    """
    Search for files matching a pattern and optionally containing specific text.

    Args:
        path: Base directory to search in
        pattern: File pattern to match (e.g., '*.txt')
        recursive: Search subdirectories recursively
        max_depth: Maximum recursion depth
        include_hidden: Include hidden files and directories
        max_results: Maximum number of results to return
        content_match: Search for files containing this text
        case_sensitive: Case-sensitive content matching
        sort_by: Sort results by: path, name, size, modified
        sort_order: Sort order: asc or desc

    Returns:
        JSON string with search results
    """
    try:
        path = os.path.abspath(path)

        if not os.path.exists(path):
            return DirectoryContents(
                path=path,
                files=[],
                count=0,
                total_size=0,
                error=f"Path does not exist: {path}",
            ).model_dump_json()

        if not os.path.isdir(path):
            return DirectoryContents(
                path=path,
                files=[],
                count=0,
                total_size=0,
                error=f"Path is not a directory: {path}",
            ).model_dump_json()

        files = []
        total_size = 0

        # Prepare content matching function
        def content_matches(file_path):
            if content_match is None:
                return True

            try:
                with open(file_path, "r", errors="ignore") as f:
                    content = f.read()
                    if case_sensitive:
                        return content_match in content
                    else:
                        return content_match.lower() in content.lower()
            except:
                return False

        # Walk the directory tree
        for root, dirs, filenames in os.walk(path):
            # Check depth
            relative_path = os.path.relpath(root, path)
            depth = 0 if relative_path == "." else relative_path.count(os.sep) + 1

            if depth > max_depth:
                dirs.clear()  # Don't go deeper
                continue

            # Skip hidden directories if not included
            if not include_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]

            # Stop recursion if not recursive
            if not recursive and root != path:
                dirs.clear()
                continue

            # Process files
            for filename in filenames:
                # Skip hidden files if not included
                if not include_hidden and filename.startswith("."):
                    continue

                # Check if file matches pattern
                if not glob.fnmatch.fnmatch(filename, pattern):
                    continue

                file_path = os.path.join(root, filename)

                # Check content if needed
                if content_match is not None and not content_matches(file_path):
                    continue

                # Get file info
                try:
                    file_info = get_file_info(file_path)
                    files.append(file_info)
                    total_size += file_info.size

                    # Check if we've reached the maximum number of results
                    if len(files) >= max_results:
                        break
                except Exception as e:
                    logger.warning(f"Error getting info for {file_path}: {str(e)}")

            # Check if we've reached the maximum number of results
            if len(files) >= max_results:
                break

        # Sort the results
        sort_key_map = {
            "path": lambda x: x.path.lower(),
            "name": lambda x: x.name.lower(),
            "size": lambda x: x.size,
            "modified": lambda x: x.modified,
        }

        sort_key = sort_key_map.get(sort_by.lower(), sort_key_map["path"])
        reverse = sort_order.lower() == "desc"

        files.sort(key=sort_key, reverse=reverse)

        return DirectoryContents(
            path=path, files=files, count=len(files), total_size=total_size
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error searching in {path}: {str(e)}")
        return DirectoryContents(
            path=path, files=[], count=0, total_size=0, error=str(e)
        ).model_dump_json()


def mcpgetfileinfo(
    path: str = Field(..., description="Path to the file or directory"),
) -> str:
    """
    Get detailed information about a file or directory.

    Args:
        path: Path to the file or directory

    Returns:
        JSON string with file information
    """
    try:
        path = os.path.abspath(path)

        if not os.path.exists(path):
            return FileOperation(
                operation="get_info",
                path=path,
                success=False,
                error=f"Path does not exist: {path}",
            ).model_dump_json()

        # Get file info
        file_info = get_file_info(path)

        # Add human-readable dates
        details = file_info.model_dump()
        details["created_date"] = datetime.fromtimestamp(file_info.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        details["modified_date"] = datetime.fromtimestamp(file_info.modified).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        details["accessed_date"] = datetime.fromtimestamp(file_info.accessed).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Add human-readable size
        size_bytes = file_info.size
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024 or unit == "TB":
                details["size_human"] = f"{size_bytes:.2f} {unit}"
                break
            size_bytes /= 1024

        return FileOperation(
            operation="get_info", path=path, success=True, details=details
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error getting info for {path}: {str(e)}")
        return FileOperation(
            operation="get_info", path=path, success=False, error=str(e)
        ).model_dump_json()


def mcpcheckpath(
    path: str = Field(..., description="Path to check"),
    check_type: str = Field(
        "exists", description="Check type: exists, file, directory, readable, writable"
    ),
) -> str:
    """
    Check if a path exists, is a file, is a directory, is readable, or is writable.

    Args:
        path: Path to check
        check_type: Check type: exists, file, directory, readable, writable

    Returns:
        JSON string with check result
    """
    try:
        path = os.path.abspath(path)

        result = False
        details = {}

        if check_type == "exists":
            result = os.path.exists(path)
            details["exists"] = result

        elif check_type == "file":
            result = os.path.isfile(path)
            details["is_file"] = result

        elif check_type == "directory":
            result = os.path.isdir(path)
            details["is_directory"] = result

        elif check_type == "readable":
            result = os.access(path, os.R_OK)
            details["is_readable"] = result

        elif check_type == "writable":
            result = os.access(path, os.W_OK)
            details["is_writable"] = result

        else:
            return FileOperation(
                operation="check",
                path=path,
                success=False,
                error=f"Invalid check type: {check_type}",
            ).model_dump_json()

        return FileOperation(
            operation="check", path=path, success=True, details=details
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error checking path {path}: {str(e)}")
        return FileOperation(
            operation="check", path=path, success=False, error=str(e)
        ).model_dump_json()


def mcpfindduplicates(
    path: str = Field(..., description="Base directory to search for duplicates"),
    recursive: bool = Field(True, description="Search subdirectories recursively"),
    method: str = Field(
        "hash", description="Method to identify duplicates: hash, size, name"
    ),
    include_hidden: bool = Field(
        False, description="Include hidden files and directories"
    ),
    max_results: int = Field(
        1000, description="Maximum number of duplicate groups to return"
    ),
) -> str:
    """
    Find duplicate files in a directory.

    Args:
        path: Base directory to search for duplicates
        recursive: Search subdirectories recursively
        method: Method to identify duplicates: hash, size, name
        include_hidden: Include hidden files and directories
        max_results: Maximum number of duplicate groups to return

    Returns:
        JSON string with duplicate files
    """
    try:
        import hashlib

        path = os.path.abspath(path)

        if not os.path.exists(path):
            return FileOperation(
                operation="find_duplicates",
                path=path,
                success=False,
                error=f"Path does not exist: {path}",
            ).model_dump_json()

        if not os.path.isdir(path):
            return FileOperation(
                operation="find_duplicates",
                path=path,
                success=False,
                error=f"Path is not a directory: {path}",
            ).model_dump_json()

        # Dictionary to store files by their identifier (hash, size, or name)
        files_by_id = {}

        # Function to calculate file hash
        def get_file_hash(file_path, block_size=65536):
            hasher = hashlib.md5()
            with open(file_path, "rb") as f:
                buf = f.read(block_size)
                while len(buf) > 0:
                    hasher.update(buf)
                    buf = f.read(block_size)
            return hasher.hexdigest()

        # Walk the directory tree
        for root, dirs, filenames in os.walk(path):
            # Skip hidden directories if not included
            if not include_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]

            # Stop recursion if not recursive
            if not recursive and root != path:
                dirs.clear()
                continue

            # Process files
            for filename in filenames:
                # Skip hidden files if not included
                if not include_hidden and filename.startswith("."):
                    continue

                file_path = os.path.join(root, filename)

                try:
                    # Get file identifier based on method
                    if method == "hash":
                        file_id = get_file_hash(file_path)
                    elif method == "size":
                        file_id = os.path.getsize(file_path)
                    elif method == "name":
                        file_id = filename
                    else:
                        raise ValueError(f"Invalid method: {method}")

                    # Add file to dictionary
                    if file_id not in files_by_id:
                        files_by_id[file_id] = []
                    files_by_id[file_id].append(file_path)
                except Exception as e:
                    logger.warning(f"Error processing {file_path}: {str(e)}")

        # Find duplicates (files with the same identifier)
        duplicates = {id: paths for id, paths in files_by_id.items() if len(paths) > 1}

        # Limit the number of duplicate groups
        duplicate_groups = list(duplicates.items())[:max_results]

        # Create result
        duplicate_files = []
        for file_id, file_paths in duplicate_groups:
            # Get file info for each duplicate
            file_infos = []
            for file_path in file_paths:
                try:
                    file_info = get_file_info(file_path)
                    file_infos.append(file_info.model_dump())
                except Exception as e:
                    logger.warning(f"Error getting info for {file_path}: {str(e)}")

            duplicate_files.append(
                {
                    "identifier": str(file_id),
                    "method": method,
                    "count": len(file_paths),
                    "files": file_infos,
                }
            )

        return FileOperation(
            operation="find_duplicates",
            path=path,
            success=True,
            details={
                "method": method,
                "duplicate_groups": len(duplicate_files),
                "duplicates": duplicate_files,
            },
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error finding duplicates in {path}: {str(e)}")
        return FileOperation(
            operation="find_duplicates", path=path, success=False, error=str(e)
        ).model_dump_json()


def mcpcompressfiles(
    source: str = Field(..., description="Source file or directory to compress"),
    destination: str = Field(..., description="Destination archive file"),
    format: str = Field(
        "zip", description="Archive format: zip, tar, gztar, bztar, xztar"
    ),
    compression_level: int = Field(
        9, description="Compression level (0-9, 9 is highest)"
    ),
    include_base_dir: bool = Field(
        True, description="Include base directory in archive"
    ),
) -> str:
    """
    Compress files or directories into an archive.

    Args:
        source: Source file or directory to compress
        destination: Destination archive file
        format: Archive format: zip, tar, gztar, bztar, xztar
        compression_level: Compression level (0-9, 9 is highest)
        include_base_dir: Include base directory in archive

    Returns:
        JSON string with operation result
    """
    try:
        source = os.path.abspath(source)
        destination = os.path.abspath(destination)

        if not os.path.exists(source):
            return FileOperation(
                operation="compress",
                path=source,
                success=False,
                error=f"Source path does not exist: {source}",
            ).model_dump_json()

        # Validate format
        valid_formats = ["zip", "tar", "gztar", "bztar", "xztar"]
        if format not in valid_formats:
            return FileOperation(
                operation="compress",
                path=source,
                success=False,
                error=f"Invalid format: {format}. Valid formats are: {', '.join(valid_formats)}",
            ).model_dump_json()

        # Create parent directories if they don't exist
        os.makedirs(os.path.dirname(destination), exist_ok=True)

        # Set compression options
        if format == "zip":
            import zipfile

            compression = zipfile.ZIP_DEFLATED

        # Compress the files
        base_name = os.path.splitext(destination)[0]
        root_dir = os.path.dirname(source)
        base_dir = os.path.basename(source) if include_base_dir else None

        archive_path = shutil.make_archive(
            base_name,
            format,
            root_dir,
            base_dir,
        )

        # Get archive info
        archive_info = get_file_info(archive_path)

        return FileOperation(
            operation="compress",
            path=source,
            success=True,
            details={
                "source": source,
                "destination": archive_path,
                "format": format,
                "archive_info": archive_info.model_dump(),
            },
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error compressing {source} to {destination}: {str(e)}")
        return FileOperation(
            operation="compress", path=source, success=False, error=str(e)
        ).model_dump_json()


def mcpextractarchive(
    source: str = Field(..., description="Source archive file"),
    destination: str = Field(..., description="Destination directory"),
    format: Optional[str] = Field(
        None, description="Archive format (auto-detect if None)"
    ),
    overwrite: bool = Field(False, description="Overwrite files in destination"),
) -> str:
    """
    Extract files from an archive.

    Args:
        source: Source archive file
        destination: Destination directory
        format: Archive format (auto-detect if None)
        overwrite: Overwrite files in destination

    Returns:
        JSON string with operation result
    """
    try:
        source = os.path.abspath(source)
        destination = os.path.abspath(destination)

        if not os.path.exists(source):
            return FileOperation(
                operation="extract",
                path=source,
                success=False,
                error=f"Source archive does not exist: {source}",
            ).model_dump_json()

        if not os.path.isfile(source):
            return FileOperation(
                operation="extract",
                path=source,
                success=False,
                error=f"Source is not a file: {source}",
            ).model_dump_json()

        # Check if destination exists
        if os.path.exists(destination) and not os.path.isdir(destination):
            return FileOperation(
                operation="extract",
                path=source,
                success=False,
                error=f"Destination is not a directory: {destination}",
            ).model_dump_json()

        # Create destination directory if it doesn't exist
        os.makedirs(destination, exist_ok=True)

        # Auto-detect format if not specified
        if format is None:
            ext = os.path.splitext(source)[1].lower()
            if ext == ".zip":
                format = "zip"
            elif ext == ".tar":
                format = "tar"
            elif ext in [".gz", ".tgz"]:
                format = "gztar"
            elif ext in [".bz2", ".tbz2"]:
                format = "bztar"
            elif ext in [".xz", ".txz"]:
                format = "xztar"
            else:
                return FileOperation(
                    operation="extract",
                    path=source,
                    success=False,
                    error=f"Could not auto-detect archive format for: {source}",
                ).model_dump_json()

        # Extract the archive
        shutil.unpack_archive(source, destination, format)

        # Count extracted files
        extracted_files = []
        total_size = 0

        for root, dirs, files in os.walk(destination):
            for file in files:
                file_path = os.path.join(root, file)
                file_size = os.path.getsize(file_path)
                extracted_files.append(file_path)
                total_size += file_size

        return FileOperation(
            operation="extract",
            path=source,
            success=True,
            details={
                "source": source,
                "destination": destination,
                "format": format,
                "extracted_files_count": len(extracted_files),
                "total_size": total_size,
            },
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error extracting {source} to {destination}: {str(e)}")
        return FileOperation(
            operation="extract", path=source, success=False, error=str(e)
        ).model_dump_json()


def mcpchangepermissions(
    path: str = Field(..., description="Path to change permissions for"),
    mode: str = Field(..., description="Permission mode (e.g., '0755', '0644')"),
    recursive: bool = Field(False, description="Apply permissions recursively"),
) -> str:
    """
    Change file or directory permissions.

    Args:
        path: Path to change permissions for
        mode: Permission mode (e.g., '0755', '0644')
        recursive: Apply permissions recursively

    Returns:
        JSON string with operation result
    """
    try:
        path = os.path.abspath(path)

        if not os.path.exists(path):
            return FileOperation(
                operation="chmod",
                path=path,
                success=False,
                error=f"Path does not exist: {path}",
            ).model_dump_json()

        # Convert mode string to integer
        if isinstance(mode, str):
            if mode.startswith("0"):
                mode_int = int(mode, 8)
            else:
                mode_int = int(mode)
        else:
            mode_int = mode

        # Change permissions
        if recursive and os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for dir in dirs:
                    os.chmod(os.path.join(root, dir), mode_int)
                for file in files:
                    os.chmod(os.path.join(root, file), mode_int)

        os.chmod(path, mode_int)

        # Get updated file info
        file_info = get_file_info(path)

        return FileOperation(
            operation="chmod",
            path=path,
            success=True,
            details={
                "mode": f"0{mode_int:o}",
                "recursive": recursive,
                "file_info": file_info.model_dump(),
            },
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error changing permissions for {path}: {str(e)}")
        return FileOperation(
            operation="chmod", path=path, success=False, error=str(e)
        ).model_dump_json()


def mcpgetdiskusage(
    path: str = Field(..., description="Path to get disk usage for"),
    human_readable: bool = Field(
        True, description="Return sizes in human-readable format"
    ),
) -> str:
    """
    Get disk usage information for a path.

    Args:
        path: Path to get disk usage for
        human_readable: Return sizes in human-readable format

    Returns:
        JSON string with disk usage information
    """
    try:
        import shutil

        path = os.path.abspath(path)

        if not os.path.exists(path):
            return FileOperation(
                operation="disk_usage",
                path=path,
                success=False,
                error=f"Path does not exist: {path}",
            ).model_dump_json()

        # Get disk usage for the path
        if os.path.isdir(path):
            total_size = 0
            file_count = 0
            dir_count = 0

            for root, dirs, files in os.walk(path):
                dir_count += len(dirs)
                file_count += len(files)

                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        total_size += os.path.getsize(file_path)
                    except (OSError, FileNotFoundError):
                        pass
        else:
            total_size = os.path.getsize(path)
            file_count = 1
            dir_count = 0

        # Get disk usage for the filesystem
        disk_usage = shutil.disk_usage(path)

        # Format sizes if human-readable
        if human_readable:

            def format_size(size_bytes):
                for unit in ["B", "KB", "MB", "GB", "TB"]:
                    if size_bytes < 1024 or unit == "TB":
                        return f"{size_bytes:.2f} {unit}"
                    size_bytes /= 1024

            details = {
                "path_size": format_size(total_size),
                "file_count": file_count,
                "directory_count": dir_count,
                "disk_total": format_size(disk_usage.total),
                "disk_used": format_size(disk_usage.used),
                "disk_free": format_size(disk_usage.free),
                "disk_percent_used": f"{disk_usage.used / disk_usage.total * 100:.1f}%",
            }
        else:
            details = {
                "path_size": total_size,
                "file_count": file_count,
                "directory_count": dir_count,
                "disk_total": disk_usage.total,
                "disk_used": disk_usage.used,
                "disk_free": disk_usage.free,
                "disk_percent_used": disk_usage.used / disk_usage.total,
            }

        return FileOperation(
            operation="disk_usage", path=path, success=True, details=details
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Error getting disk usage for {path}: {str(e)}")
        return FileOperation(
            operation="disk_usage", path=path, success=False, error=str(e)
        ).model_dump_json()


# Main function
if __name__ == "__main__":
    run_mcp_server(
        "Filesystem Server",
        funcs=[
            mcplistdir,
            mcpreadfile,
            mcpwritefile,
            mcpcreatedirectory,
            mcpdeletepath,
            mcpcopypath,
            mcpmovepath,
            mcpsearchfiles,
            mcpgetfileinfo,
            mcpcheckpath,
            mcpfindduplicates,
            mcpcompressfiles,
            mcpextractarchive,
            mcpchangepermissions,
            mcpgetdiskusage,
        ],
        port=2005,
    )
