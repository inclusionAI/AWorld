# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.trace.context_manager import TraceManager
from aworld.trace.constants import (
    SPAN_NAME_PREFIX_EVENT,
    SPAN_NAME_PREFIX_EVENT_AGENT,
    SPAN_NAME_PREFIX_EVENT_TOOL,
    SPAN_NAME_PREFIX_EVENT_TASK,
    SPAN_NAME_PREFIX_EVENT_OUTPUT,
    SPAN_NAME_PREFIX_EVENT_OTHER,
    SPAN_NAME_PREFIX_AGENT,
    SPAN_NAME_PREFIX_TOOL,
    ATTRIBUTES_MESSAGE_RUN_TYPE_KEY,
    SPAN_NAME_PREFIX_TASK,
    RunType
)
from aworld.trace.instrumentation.agent import get_agent_span_attributes
from aworld.trace.instrumentation.tool import get_tool_name, get_tool_span_attributes
from aworld.trace.instrumentation import semconv
from aworld.trace.instrumentation.uni_llmmodel.model_response_parse import covert_to_jsonstr
from aworld.trace.config import configure, ObservabilityConfig
from typing import Callable, Any


def get_span_name_from_message(message: 'aworld.core.event.base.Message') -> tuple[str, RunType]:
    from aworld.core.event.base import Constants
    if message.category == Constants.AGENT:
        receiver = message.receiver or message.id
        return (SPAN_NAME_PREFIX_EVENT_AGENT + receiver, RunType.AGNET)
    if message.category == Constants.TOOL:
        action = message.payload
        if isinstance(action, (list, tuple)):
            action = action[0]
        if action:
            tool_name, run_type = get_tool_name(action.tool_name, action)
            return (SPAN_NAME_PREFIX_EVENT_TOOL + tool_name, run_type)
        return (SPAN_NAME_PREFIX_EVENT_TOOL, RunType.TOOL)
    if message.category == Constants.TASK:
        if message.topic:
            return (SPAN_NAME_PREFIX_EVENT_TASK + message.topic, RunType.OTHER)
        else:
            return (SPAN_NAME_PREFIX_EVENT_TASK, RunType.OTHER)
    if message.category == Constants.OUTPUT:
        output_type = message.payload.output_type()
        if output_type:
            if output_type == "step" and hasattr(message.payload, "status"):
                status = message.payload.status
                return (SPAN_NAME_PREFIX_EVENT_OUTPUT + output_type + "." + status, RunType.OTHER)
            return (SPAN_NAME_PREFIX_EVENT_OUTPUT + output_type, RunType.OTHER)
        return (SPAN_NAME_PREFIX_EVENT_OUTPUT, RunType.OTHER)
    if message.category:
        return (SPAN_NAME_PREFIX_EVENT + message.category, RunType.OTHER)
    return (SPAN_NAME_PREFIX_EVENT_OTHER, RunType.OTHER)


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
            "event.header": str(message.headers),
            semconv.SESSION_ID: message.session_id,
            semconv.TRACE_ID: message.context.trace_id
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
    attributes[semconv.TRACE_ID] = message.context.trace_id
    span_name = handler.__name__
    if message:
        run_type = RunType.OTHER
        if message.category == Constants.AGENT:
            run_type = RunType.AGNET
            attributes.update(get_agent_span_attributes(handler.__self__, message))
            if attributes.get(semconv.AGENT_NAME):
                span_name = SPAN_NAME_PREFIX_AGENT + attributes.get(semconv.AGENT_NAME) + "." + span_name
            else:
                span_name = SPAN_NAME_PREFIX_AGENT + span_name
        if message.category == Constants.TOOL:
            run_type = RunType.TOOL
            attributes.update(get_tool_span_attributes(handler.__self__, message))
            if attributes.get(semconv.TOOL_NAME):
                span_name = SPAN_NAME_PREFIX_TOOL + attributes.get(semconv.TOOL_NAME) + "." + span_name
            else:
                span_name = SPAN_NAME_PREFIX_TOOL + span_name
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


def task_span(session_id: str, task: 'aworld.core.task.Task' = None, attributes: dict = None):
    attributes = attributes or {}
    if task:
        message_span_attribute = {
            semconv.SESSION_ID: task.session_id,
            semconv.TASK_ID: task.id,
            semconv.TASK_INPUT: task.input,
            semconv.TASK_IS_SUB_TASK: task.is_sub_task,
            semconv.TASK_GROUP_ID: task.group_id,
            semconv.TASK: covert_to_jsonstr(task),
            semconv.TRACE_ID: task.trace_id
        }
        message_span_attribute.update(attributes)
        return GLOBAL_TRACE_MANAGER.span(
            span_name=SPAN_NAME_PREFIX_TASK + task.id,
            attributes=message_span_attribute,
            run_type=RunType.TASK
        )
    else:
        message_span_attribute = {
            semconv.SESSION_ID: task.session_id
        }
        return GLOBAL_TRACE_MANAGER.span(
            span_name=SPAN_NAME_PREFIX_TASK + session_id,
            attributes=attributes,
            run_type=RunType.TASK
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
