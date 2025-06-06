# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import traceback
from typing import Sequence, Union
from aworld.core.common import ActionModel
from aworld.trace.context_manager import TraceManager, trace_configure
from aworld.trace.constants import RunType
from aworld.logs.log import set_log_provider, instrument_logging
from aworld.logs.util import logger, trace_logger

if os.getenv("LOGFIRE_WRITE_TOKEN"):
    trace_configure(
        backends=["logfire"],
        write_token=os.getenv("LOGFIRE_WRITE_TOKEN")
    )
    set_log_provider(provider="otlp", backend="logfire",
                     write_token=os.getenv("LOGFIRE_WRITE_TOKEN"))
elif os.getenv("OTLP_TRACES_ENDPOINT"):
    trace_configure(
        backends=["other_otlp"]
    )
else:
    logger.warning("LOGFIRE_WRITE_TOKEN is not set, using memory backend")
    trace_configure(
        backends=["memory"]
    )

instrument_logging(trace_logger)


def get_tool_name(tool_name: str,
                  action: Union[ActionModel, Sequence[ActionModel]]) -> tuple[str, RunType]:
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


GLOBAL_TRACE_MANAGER: TraceManager = TraceManager()
span = GLOBAL_TRACE_MANAGER.span
func_span = GLOBAL_TRACE_MANAGER.func_span
auto_tracing = GLOBAL_TRACE_MANAGER.auto_tracing
get_current_span = GLOBAL_TRACE_MANAGER.get_current_span
new_manager = GLOBAL_TRACE_MANAGER.get_current_span
