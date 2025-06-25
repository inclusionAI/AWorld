# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import traceback
from typing import Sequence, Union
from aworld.trace.context_manager import TraceManager
from aworld.trace.constants import RunType
from aworld.logs.util import logger
from aworld.trace.config import configure, ObservabilityConfig


def get_tool_name(tool_name: str,
                  action: Union['ActionModel', Sequence['ActionModel']]) -> tuple[str, RunType]:
    if tool_name == "mcp" and action:
        try:
            if isinstance(action, (list, tuple)):
                action = action[0]
            mcp_name = action.action_name.split("__")[0]
            return (mcp_name, RunType.MCP)
        except ValueError:
            logger.warning(traceback.format_exc())
            return (tool_name, RunType.MCP)
    return (tool_name, RunType.TOOL)


def get_span_name_from_message(message: 'aworld.core.event.base.Message') -> tuple[str, RunType]:
    from aworld.core.event.base import Constants
    span_name = (message.receiver or message.id)
    if message.category == Constants.AGENT:
        return (span_name, RunType.AGNET)
    if message.category == Constants.TOOL:
        action = message.payload
        if isinstance(action, (list, tuple)):
            action = action[0]
        if action:
            tool_name, run_type = get_tool_name(action.tool_name, action)
            return (tool_name, run_type)
        return (span_name, RunType.TOOL)
    return (span_name, RunType.OTHER)


def message_span(message: 'aworld.core.event.base.Message' = None, attributes: dict = None):
    if message:
        span_name, run_type = get_span_name_from_message(message)
        message_span_attribute = {
            "event.payload": str(message.payload),
            "event.topic": message.topic or "",
            "event.receiver": message.receiver or "",
            "event.sender": message.sender or "",
            "event.category": message.category,
            "event.id": message.id,
            "event.session_id": message.session_id
        }
        message_span_attribute.update(attributes or {})
        return GLOBAL_TRACE_MANAGER.span(
            span_name=f"{run_type.value.lower()}_event_{span_name}",
            attributes=message_span_attribute,
            run_type=run_type
        )
    else:
        raise ValueError("message_span message is None")


GLOBAL_TRACE_MANAGER: TraceManager = TraceManager()
span = GLOBAL_TRACE_MANAGER.span
func_span = GLOBAL_TRACE_MANAGER.func_span
auto_tracing = GLOBAL_TRACE_MANAGER.auto_tracing
get_current_span = GLOBAL_TRACE_MANAGER.get_current_span
new_manager = GLOBAL_TRACE_MANAGER.get_current_span

__all__ = [
    "span",
    "func_span",
    "message_span",
    "auto_tracing",
    "get_current_span",
    "new_manager",
    "RunType",
    "configure",
    "ObservabilityConfig"
]
