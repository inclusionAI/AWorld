# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import traceback
from typing import Sequence, Union
# from aworld.core.common import ActionModel
from aworld.trace.context_manager import TraceManager
from aworld.trace.constants import RunType
from aworld.logs.util import logger
from aworld.trace.config import configure, ObservabilityConfig


# def get_tool_name(tool_name: str,
#                   action: Union[ActionModel, Sequence[ActionModel]]) -> tuple[str, RunType]:
#     if tool_name == "mcp" and action:
#         try:
#             if isinstance(action, (list, tuple)):
#                 action = action[0]
#             mcp_name = action.action_name.split("__")[0]
#             return (mcp_name, RunType.MCP)
#         except ValueError:
#             logger.warning(traceback.format_exc())
#             return (tool_name, RunType.MCP)
#     return (tool_name, RunType.TOOL)


GLOBAL_TRACE_MANAGER: TraceManager = TraceManager()
span = GLOBAL_TRACE_MANAGER.span
func_span = GLOBAL_TRACE_MANAGER.func_span
auto_tracing = GLOBAL_TRACE_MANAGER.auto_tracing
get_current_span = GLOBAL_TRACE_MANAGER.get_current_span
new_manager = GLOBAL_TRACE_MANAGER.get_current_span

__all__ = [
    "span",
    "func_span",
    "auto_tracing",
    "get_current_span",
    "new_manager",
    "RunType",
    "configure",
    "ObservabilityConfig"
]
