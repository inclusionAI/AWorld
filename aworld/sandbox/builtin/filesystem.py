# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Builtin filesystem tool implementation."""

from pathlib import Path
from typing import Optional, List, Any, Tuple

from aworld.logs.util import logger
from aworld.sandbox.builtin.base import BuiltinTool, SERVICE_FILESYSTEM
from aworld.sandbox.builtin.utils import path_utils, file_ops


class FilesystemTool(BuiltinTool):
    """Builtin filesystem tool implementation."""
    
    def __init__(self, allowed_directories: Optional[List[str]] = None):
        """
        Args:
            allowed_directories: List of allowed directory paths. If None, uses default workspace.
        """
        super().__init__(SERVICE_FILESYSTEM)
        # Normalize input: convert string to list, ensure it's a list
        if isinstance(allowed_directories, str):
            allowed_directories = [allowed_directories]
        elif allowed_directories is not None and not isinstance(allowed_directories, list):
            raise TypeError(f"allowed_directories must be a list of strings or None, got {type(allowed_directories)}")
        
        self.allowed_directories = allowed_directories or self._get_default_workspaces()
        logger.debug(f"FilesystemTool initialized with allowed_directories: {self.allowed_directories} "
                    f"(input was: {allowed_directories})")
        # Ensure default workspace roots actually exist so they are usable immediately
        for d in self.allowed_directories:
            try:
                Path(d).mkdir(parents=True, exist_ok=True)
            except Exception:
                # Best-effort: 如果创建失败，不影响后续由 validate_path 做权限/存在性检查
                logger.warning(f"Failed to create default workspace directory: {d}")
    
    def update_allowed_directories(self, allowed_directories: Optional[List[str]] = None):
        """Update allowed directories for filesystem operations.
        
        Args:
            allowed_directories: List of allowed directory paths. If None, uses default workspace.
        """
        # Normalize input: convert string to list, ensure it's a list
        if isinstance(allowed_directories, str):
            allowed_directories = [allowed_directories]
        elif allowed_directories is not None and not isinstance(allowed_directories, list):
            raise TypeError(f"allowed_directories must be a list of strings or None, got {type(allowed_directories)}")
        
        self.allowed_directories = allowed_directories or self._get_default_workspaces()
        # Ensure workspace roots actually exist
        for d in self.allowed_directories:
            try:
                Path(d).mkdir(parents=True, exist_ok=True)
            except Exception:
                logger.warning(f"Failed to create workspace directory: {d}")
        logger.info(f"Updated FilesystemTool allowed_directories to: {self.allowed_directories}")
    
    def _get_default_workspaces(self) -> List[str]:
        """Get default workspace directories."""
        home_dir = Path.home()
        return [
            str(home_dir / "workspace"),
            str(home_dir / "aworld_workspace")
        ]
    
    async def _validate_path_safe(self, path: str) -> Tuple[bool, str]:
        """Safely validate path and return (is_valid, result).
        
        Args:
            path: Path to validate
            
        Returns:
            Tuple of (is_valid, result_path_or_error_message)
        """
        try:
            valid_path = await path_utils.validate_path(path, self.allowed_directories)
            return True, valid_path
        except ValueError as e:
            # Return error message instead of raising exception
            return False, str(e)
    
    async def execute(self, tool_name: str, **kwargs) -> Any:
        """Execute a filesystem tool."""
        method = getattr(self, tool_name, None)
        if not method or not callable(method):
            raise ValueError(f"Unknown tool: {tool_name}")
        return await method(**kwargs)
    
    async def read_file(self, path: str, head: Optional[int] = None, tail: Optional[int] = None) -> str:
        """Read text file content.
        
        Args:
            path: File path to read
            head: Return only first N lines
            tail: Return only last N lines
            
        Returns:
            File content as string, or error message if path is not allowed
        """
        if head and tail:
            raise ValueError("Cannot specify both head and tail")
        
        is_valid, result = await self._validate_path_safe(path)
        if not is_valid:
            return f"Error: {result}"
        
        valid_path = result
        if tail:
            return await file_ops.tail_file(valid_path, tail)
        elif head:
            return await file_ops.head_file(valid_path, head)
        else:
            return await file_ops.read_file(valid_path)
    
    async def write_file(self, path: str, content: str) -> str:
        """Create or overwrite a file.
        
        Args:
            path: File path to write
            content: File content
            
        Returns:
            Success message, or error message if path is not allowed
        """
        is_valid, result = await self._validate_path_safe(path)
        if not is_valid:
            return f"Error: {result}"
        
        valid_path = result
        await file_ops.write_file(valid_path, content)
        return f"Successfully wrote to {path}"
    
    async def edit_file(self, path: str, edits: List[dict], dryRun: bool = False) -> str:
        """Edit file with text replacements.
        
        Args:
            path: File path to edit
            edits: List of edit operations with oldText and newText
            dryRun: Preview changes without applying
            
        Returns:
            Diff text showing changes, or error message if path is not allowed
        """
        is_valid, result = await self._validate_path_safe(path)
        if not is_valid:
            return f"Error: {result}"
        
        valid_path = result
        return await file_ops.apply_edits(valid_path, edits, dryRun)
    
    async def create_directory(self, path: str) -> str:
        """Create directory.
        
        Args:
            path: Directory path to create
            
        Returns:
            Success message, or error message if path is not allowed
        """
        is_valid, result = await self._validate_path_safe(path)
        if not is_valid:
            return f"Error: {result}"
        
        valid_path = result
        Path(valid_path).mkdir(parents=True, exist_ok=True)
        return f"Successfully created directory {path}"
    
    async def list_directory(self, path: str) -> str:
        """List directory contents.
        
        Args:
            path: Directory path to list
            
        Returns:
            Directory listing as string, or error message if path is not allowed
        """
        is_valid, result = await self._validate_path_safe(path)
        if not is_valid:
            return f"Error: {result}"
        
        valid_path = result
        entries = []
        for entry in Path(valid_path).iterdir():
            prefix = "[DIR]" if entry.is_dir() else "[FILE]"
            entries.append(f"{prefix} {entry.name}")
        return "\n".join(entries)
    
    async def move_file(self, source: str, destination: str) -> str:
        """Move or rename file.
        
        Args:
            source: Source path
            destination: Destination path
            
        Returns:
            Success message, or error message if path is not allowed
        """
        is_valid_source, result_source = await self._validate_path_safe(source)
        if not is_valid_source:
            return f"Error: {result_source}"
        
        is_valid_dest, result_dest = await self._validate_path_safe(destination)
        if not is_valid_dest:
            return f"Error: {result_dest}"
        
        valid_source = result_source
        valid_dest = result_dest
        Path(valid_source).rename(valid_dest)
        return f"Successfully moved {source} to {destination}"
    
    async def list_allowed_directories(self) -> str:
        """List allowed directories.
        
        Returns:
            List of allowed directories
        """
        text = "Allowed directories:\n" + "\n".join(self.allowed_directories)
        return text
