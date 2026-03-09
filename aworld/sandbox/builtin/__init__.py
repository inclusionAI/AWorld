# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Builtin tool config and routing for Sandbox. Tool implementations live in tool_servers (MCP)."""

from aworld.sandbox.builtin.base import (
    BuiltinTool,
    SERVICE_FILESYSTEM,
    SERVICE_TERMINAL,
    SERVICE_TOOL_MAPPING,
    FILESYSTEM_TOOL_MAPPING,
    TERMINAL_TOOL_MAPPING,
    builtin_tool,
)
from aworld.sandbox.builtin.router import BuiltinToolRouter
from aworld.sandbox.builtin.validator import BuiltinToolValidator
from aworld.sandbox.builtin.exceptions import (
    BuiltinToolError,
    ToolNotAvailableError,
    ToolNotConfiguredError,
)

__all__ = [
    # Base classes
    "BuiltinTool",
    "builtin_tool",
    # Router and validator
    "BuiltinToolRouter",
    "BuiltinToolValidator",
    # Exceptions
    "BuiltinToolError",
    "ToolNotAvailableError",
    "ToolNotConfiguredError",
    # Constants
    "SERVICE_FILESYSTEM",
    "SERVICE_TERMINAL",
    "SERVICE_TOOL_MAPPING",
    "FILESYSTEM_TOOL_MAPPING",
    "TERMINAL_TOOL_MAPPING",
]

