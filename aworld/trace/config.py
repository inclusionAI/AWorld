import os
from dataclasses import dataclass, field

from typing import Sequence, Optional
from aworld.trace.span_cosumer import SpanConsumer
from aworld.trace.context_manager import trace_configure
from aworld.metrics.context_manager import MetricContext
from aworld.trace.instrumentation.uni_llmmodel import LLMModelInstrumentor
from aworld.trace.instrumentation.eventbus import EventBusInstrumentor
from aworld.trace.instrumentation.agent import AgentInstrumentor
from aworld.trace.instrumentation.tool import ToolInstrumentor
from aworld.logs.log import set_log_provider
from aworld.trace.opentelemetry.memory_storage import TraceStorage
from aworld.logs.util import trace_logger


def backend_list():
    return ["memory"]


def logger_list():
    return []


@dataclass
class ObservabilityConfig():
    '''
    Observability configuration
    '''
    trace_provider: Optional[str] = field(default="otlp")
    trace_backends: Optional[Sequence[str]] = field(default_factory=backend_list)
    trace_base_url: Optional[str] = field(default=None)
    trace_write_token: Optional[str] = field(default=None)
    trace_span_consumers: Optional[Sequence[SpanConsumer]] = field(default_factory=list)
    trace_storage: Optional[TraceStorage] = field(default=None)
    # whether to start the trace service
    trace_server_enabled: Optional[bool] = field(default=False)
    trace_server_port: Optional[int] = field(default=7079)
    metrics_provider: Optional[str] = field(default=None)
    metrics_backend: Optional[str] = field(default=None)
    metrics_base_url: Optional[str] = field(default=None)
    metrics_write_token: Optional[str] = field(default=None)
    # whether to instrument system metrics
    metrics_system_enabled: Optional[bool] = field(default=False)
    logs_provider: Optional[str] = field(default=None)
    logs_backend: Optional[str] = field(default=None)
    logs_base_url: Optional[str] = field(default=None)
    logs_write_token: Optional[str] = field(default=None)
    # The loggers that need to record the log as a span
    logs_trace_instrumented_loggers: Sequence = field(default_factory=logger_list)


def configure(config: ObservabilityConfig = None):
    if config is None:
        config = ObservabilityConfig()
    _trace_configure(config)
    _metrics_configure(config)
    # _log_configure(config)
    LLMModelInstrumentor().instrument()
    EventBusInstrumentor().instrument()
    AgentInstrumentor().instrument()
    ToolInstrumentor().instrument()


def _trace_configure(config: ObservabilityConfig):
    if not config.trace_base_url and config.trace_provider == "otlp":
        if "logfire" in config.trace_backends:
            config.trace_base_url = os.getenv("LOGFIRE_WRITE_TOKEN")
        elif os.getenv("OTLP_TRACES_ENDPOINT"):
            config.trace_base_url = os.getenv("OTLP_TRACES_ENDPOINT")
            config.trace_backends.append("other_otlp")

    trace_configure(
        provider=config.trace_provider,
        backends=config.trace_backends,
        base_url=config.trace_base_url,
        write_token=config.trace_write_token,
        span_consumers=config.trace_span_consumers,
        server_enabled=config.trace_server_enabled,
        server_port=config.trace_server_port,
        storage=config.trace_storage
    )


def _metrics_configure(config: ObservabilityConfig):
    if config.metrics_provider and config.metrics_backend:
        MetricContext.configure(
            provider=config.metrics_provider,
            backend=config.metrics_backend,
            base_url=config.metrics_base_url,
            write_token=config.metrics_write_token,
            metrics_system_enabled=config.metrics_system_enabled
        )


def _log_configure(config: ObservabilityConfig):
    if config.logs_provider and config.logs_backend:
        if config.logs_backend == "logfire" and not config.logs_write_token:
            config.logs_write_token = os.getenv("LOGFIRE_WRITE_TOKEN")
        set_log_provider(provider=config.logs_provider,
                         backend=config.logs_backend,
                         base_url=config.logs_base_url,
                         write_token=config.logs_write_token)
    if _has_loguru():
        instrument_loguru(config.logs_trace_instrumented_loggers)
    elif _has_logging():
        _instrument_logging(config.logs_trace_instrumented_loggers)


def _instrument_logging(logs_trace_instrumented_loggers: Sequence[str]) -> None:
    """Instrument the logger."""
    from aworld.logs.instrument.logging_instrument import instrument_logging
    import logging

    if not logs_trace_instrumented_loggers:
        instrument_logging()
    else:
        for logger in logs_trace_instrumented_loggers:
            if isinstance(logger, logging.Logger):
                instrument_logging(logger)


def instrument_loguru(logs_trace_instrumented_loggers) -> None:
    """Instrument the logger."""
    from aworld.logs.instrument.loguru_instrument import instrument_loguru, instrument_loguru_base_logger
    from aworld.logs.util import AWorldLogger

    instrument_loguru_base_logger()
    need_instrument_logger_tags = set()
    for logger in logs_trace_instrumented_loggers:
        if isinstance(logger, AWorldLogger):
            need_instrument_logger_tags.add(logger.tag)
    instrument_loguru(need_instrument_logger_tags)


def _has_loguru() -> bool:
    try:
        import loguru
        return True
    except ImportError:
        return False


def _has_logging() -> bool:
    try:
        import logging
        return True
    except ImportError:
        return False
