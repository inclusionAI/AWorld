# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Custom exceptions for builtin tools."""


class BuiltinToolError(Exception):
    """Base exception for builtin tool errors."""
    pass


class ToolNotAvailableError(BuiltinToolError):
    """Raised when a tool is not available in user-configured MCP server."""
    
    def __init__(self, service_name: str, tool_name: str):
        self.service_name = service_name
        self.tool_name = tool_name
        message = (
            f"Tool '{tool_name}' not found in user-configured "
            f"'{service_name}' server. Please use sandbox.call_tool() "
            f"to call custom tools, or ensure the server provides '{tool_name}'."
        )
        super().__init__(message)


class ToolNotConfiguredError(BuiltinToolError):
    """Raised when a service is not configured and no builtin fallback is available."""
    
    def __init__(self, service_name: str):
        self.service_name = service_name
        message = (
            f"Service '{service_name}' not configured and "
            f"no builtin fallback available."
        )
        super().__init__(message)

