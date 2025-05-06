# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from aworld.trace.context_manager import TraceManager, trace_configure
from aworld.logs.log import set_log_provider, instrument_logging
from aworld.logs.util import logger, trace_logger

if os.getenv("LOGFIRE_WRITE_TOKEN"):
    trace_configure(
        backends=["logfire"],
        write_token=os.getenv("LOGFIRE_WRITE_TOKEN")
    )
    set_log_provider(provider="otlp", backend="logfire", write_token=os.getenv("LOGFIRE_WRITE_TOKEN"))
else:
    logger.warning("LOGFIRE_WRITE_TOKEN is not set, using memory backend")
    trace_configure(
        backends=["memory"]
    )

instrument_logging(trace_logger)

GLOBAL_TRACE_MANAGER: TraceManager = TraceManager()
span = GLOBAL_TRACE_MANAGER.span
func_span = GLOBAL_TRACE_MANAGER.func_span
auto_tracing = GLOBAL_TRACE_MANAGER.auto_tracing
get_current_span = GLOBAL_TRACE_MANAGER.get_current_span
new_manager = GLOBAL_TRACE_MANAGER.get_current_span