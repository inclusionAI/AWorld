# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.core.tool.base import Tool, AsyncTool
from aworld.core.tool.action import ExecutableAction
from aworld.utils.common import scan_packages

scan_packages("aworld.tools", [Tool, AsyncTool, ExecutableAction])

from aworld.tools.function_tools import FunctionTools, get_function_tools, list_function_tools
from aworld.tools.function_tools_adapter import FunctionToolsMCPAdapter, get_function_tools_mcp_adapter
from aworld.tools.function_tools_executor import FunctionToolsExecutor

LOCAL_TOOLS_ENV_VAR = "LOCAL_TOOLS_ENV_VAR"
LOCAL_TOOL_ENTRY_SEPARATOR = ";"
LOCAL_TOOL_PATH_SEPARATOR = "|"


def encode_local_tool_entry(action_file: str, tool_file: str) -> str:
    return f"{action_file}{LOCAL_TOOL_PATH_SEPARATOR}{tool_file}"


def parse_local_tool_entries(value: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    if not value:
        return entries

    for entry in value.split(LOCAL_TOOL_ENTRY_SEPARATOR):
        raw = entry.strip()
        if not raw:
            continue

        if LOCAL_TOOL_PATH_SEPARATOR in raw:
            action_file, tool_file = raw.split(LOCAL_TOOL_PATH_SEPARATOR, 1)
        else:
            action_file = raw
            tool_file = raw.replace("_action.py", ".py")

        entries.append((action_file, tool_file))

    return entries
