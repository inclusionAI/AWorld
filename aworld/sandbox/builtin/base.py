# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Base classes and tool mappings for builtin tools."""

import abc
from typing import Dict, Any, Callable, Optional
from functools import wraps

# 工具名映射：Sandbox方法名 -> MCP工具名
FILESYSTEM_TOOL_MAPPING = {
    "read_file": "read_file",
    "write_file": "write_file",
    "edit_file": "replace_in_file",
    "replace_in_file": "replace_in_file",
    "edit_file_range": "edit_file_range",
    "upload_file": "upload_file",
    "download_file": "download_file",
    "parse_file": "parse_file",
    "create_directory": "create_directory",
    "list_directory": "list_directory",
    "move_file": "move_file",
    "list_allowed_directories": "list_allowed_directories",
}

TERMINAL_TOOL_MAPPING = {
    "run_code": "run_code",
}

# 服务名称
SERVICE_FILESYSTEM = "filesystem"
SERVICE_TERMINAL = "terminal"

# 服务到工具映射的映射
SERVICE_TOOL_MAPPING = {
    SERVICE_FILESYSTEM: FILESYSTEM_TOOL_MAPPING,
    SERVICE_TERMINAL: TERMINAL_TOOL_MAPPING,
}


class BuiltinTool(abc.ABC):
    """Base class for builtin tool implementations."""
    
    def __init__(self, service_name: str):
        """
        Args:
            service_name: Service name (e.g., "filesystem", "terminal")
        """
        self.service_name = service_name
    
    @abc.abstractmethod
    async def execute(self, tool_name: str, **kwargs) -> Any:
        """Execute a tool.
        
        Args:
            tool_name: Tool name to execute
            **kwargs: Tool parameters
            
        Returns:
            Tool execution result
        """
        pass


def builtin_tool(service: str, tool_name: str, fallback_to_builtin: bool = True):
    """
    Decorator to mark a method as a builtin tool method.
    
    Args:
        service: Service name (filesystem/terminal)
        tool_name: MCP tool name
        fallback_to_builtin: Whether to use builtin implementation if user not configured
    
    Usage:
        @builtin_tool(service="filesystem", tool_name="read_file")
        async def read_file(self, path: str, ...):
            # Builtin implementation
            pass
    """
    def decorator(func: Callable) -> Callable:
        # Mark function as builtin tool method
        func._is_builtin_tool = True
        func._service_name = service
        func._tool_name = tool_name
        func._fallback = fallback_to_builtin
        return func
    return decorator

