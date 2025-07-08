# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.trace.context_manager import TraceManager
from aworld.trace.constants import (
    SPAN_NAME_PREFIX_EVENT_AGENT,
    SPAN_NAME_PREFIX_EVENT_TOOL,
    SPAN_NAME_PREFIX_EVENT_TASK,
    SPAN_NAME_PREFIX_EVENT_OUTPUT,
    SPAN_NAME_PREFIX_EVENT_OTHER,
    SPAN_NAME_PREFIX_AGENT,
    SPAN_NAME_PREFIX_TOOL,
    ATTRIBUTES_MESSAGE_RUN_TYPE_KEY,
    RunType
)
from aworld.trace.instrumentation.agent import get_agent_span_attributes
from aworld.trace.instrumentation.tool import get_tool_name, get_tool_span_attributes
from aworld.trace.config import configure, ObservabilityConfig
from typing import Callable, Any


def get_span_name_from_message(message: 'aworld.core.event.base.Message') -> tuple[str, RunType]:
    from aworld.core.event.base import Constants
    span_name = (message.receiver or message.id)
    if message.category == Constants.AGENT:
        return (SPAN_NAME_PREFIX_EVENT_AGENT + span_name, RunType.AGNET)
    if message.category == Constants.TOOL:
        action = message.payload
        if isinstance(action, (list, tuple)):
            action = action[0]
        if action:
            tool_name, run_type = get_tool_name(action.tool_name, action)
            return (SPAN_NAME_PREFIX_EVENT_TOOL + tool_name, run_type)
        return (SPAN_NAME_PREFIX_EVENT_TOOL + span_name, RunType.TOOL)
    if message.category == Constants.TASK:
        if message.topic:
            return (SPAN_NAME_PREFIX_EVENT_TASK + message.topic + "_" + span_name, RunType.OTHER)
        else:
            return (SPAN_NAME_PREFIX_EVENT_TASK + span_name, RunType.OTHER)
    if message.category == Constants.OUTPUT:
        return (SPAN_NAME_PREFIX_EVENT_OUTPUT + span_name, RunType.OTHER)
    return (SPAN_NAME_PREFIX_EVENT_OTHER + span_name, RunType.OTHER)


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
            span_name=span_name,
            attributes=message_span_attribute,
            run_type=run_type
        )
    else:
        raise ValueError("message_span message is None")


def handler_span(message: 'aworld.core.event.base.Message' = None, handler: Callable[..., Any] = None, attributes: dict = None):
    from aworld.core.event.base import Constants
    attributes = attributes or {}
    span_name = handler.__name__
    if message:
        run_type = RunType.OTHER
        if message.category == Constants.AGENT:
            span_name = SPAN_NAME_PREFIX_AGENT + span_name
            run_type = RunType.AGNET
            attributes.update(get_agent_span_attributes(handler.__self__, message))
        if message.category == Constants.TOOL:
            span_name = SPAN_NAME_PREFIX_TOOL + span_name
            run_type = RunType.TOOL
            attributes.update(get_tool_span_attributes(handler.__self__, message))
            if attributes.get(ATTRIBUTES_MESSAGE_RUN_TYPE_KEY):
                run_type = RunType[attributes.get(ATTRIBUTES_MESSAGE_RUN_TYPE_KEY)]
        return GLOBAL_TRACE_MANAGER.span(
            span_name=span_name,
            attributes=attributes,
            run_type=run_type
        )
    else:
        return GLOBAL_TRACE_MANAGER.span(
            span_name=span_name,
            attributes=attributes
        )


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
