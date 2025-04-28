import time
from typing import Union
from logging import Logger, Filter, LogRecord, NOTSET, Handler, Formatter, StreamHandler
from abc import ABC
from typing import Optional
from aworld.trace.trace import get_tracer_provider_silent, AttributeValueType, Tracer

TRACE_LOG_FORMAT = '%(asctime)s - [%(trace_id)s] - [%(span_id)s] - %(name)s - %(levelname)s - %(message)s'
SPECIAL_TRACE_LOG_FORMAT = '%(asctime)s - [trace_%(trace_id)s] - [%(span_id)s] - %(name)s - %(levelname)s - %(message)s'

class LoggerProvider(ABC):
    """
    A logger provider is a factory for loggers.
    """


_GLOBAL_LOG_PROVIDER: Optional[LoggerProvider] = None

def set_log_provider(provider: str = "otlp",
                     backend: str = "logfire",
                     base_url: str = None,
                     write_token: str = None,
                     **kwargs
):
    """
    Set the global log provider.
    """

    global _GLOBAL_LOG_PROVIDER

    if provider == "otlp":
        from .opentelemetry.otlp_log import OTLPLoggerProvider
        _GLOBAL_LOG_PROVIDER = OTLPLoggerProvider(backend=backend,
                                                  base_url=base_url,
                                                  write_token=write_token,
                                                  **kwargs)



def get_log_provider() -> LoggerProvider:
    """
    Get the global log provider.
    """
    global _GLOBAL_LOG_PROVIDER
    if _GLOBAL_LOG_PROVIDER is None:
        raise ValueError("No log provider has been set.")
    return _GLOBAL_LOG_PROVIDER


def instrumentLogging(logger: Logger, level: Union[int, str] = NOTSET) -> None:
    """
    Instrument the logger.
    """
    for handler in logger.root.handlers:
        if not any(isinstance(filter, TraceLoggingFilter) for filter in handler.filters):
            handler.setFormatter(Formatter(TRACE_LOG_FORMAT))
            handler.addFilter(TraceLoggingFilter())

    if not logger.handlers:
        print("No handlers found, adding a StreamHandler. logger=", logger.name)
        handler = StreamHandler()
        handler.setFormatter(Formatter(SPECIAL_TRACE_LOG_FORMAT))
        handler.addFilter(TraceLoggingFilter())
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:
            if not any(isinstance(filter, TraceLoggingFilter) for filter in handler.filters):
                handler.setFormatter(Formatter(SPECIAL_TRACE_LOG_FORMAT))
                handler.addFilter(TraceLoggingFilter())
    logger.propagate = False
    logger.addHandler(TraceLogginHandler(level))

class TraceLoggingFilter(Filter):
    """
    A filter that adds trace information to log records.
    """

    def filter(self, record: LogRecord) -> bool:
        """
        Add trace information to the log record.
        """
        trace = get_tracer_provider_silent()
        if trace:
            span = trace.get_current_span()
            record.trace_id = span.get_trace_id() if span else None
            record.span_id = span.get_span_id() if span else None
        return True


class TraceLogginHandler(Handler):
    """
    A handler class which writes logging records, appropriately formatted,
    to a stream. Note that this class does not close the stream, as
    sys.stdout or sys.stderr may be used.
    """
    def __init__(self,
                 level: Union[int, str] = NOTSET,
                 tracer_name: str = "aworld.log",
    ) -> None:
        """
        Initialize the handler.
        """
        super().__init__(level=level)
        self._tracer_name = tracer_name
        # self.streamHandler = StreamHandler()
        self._tracer: Tracer = None

    def emit(self, record: LogRecord) -> None:
        """
        Emit a record.
        """
        trace = get_tracer_provider_silent()
        if not trace or not trace.get_current_span() or not trace.get_current_span().is_recording():
            return

        if not self._tracer:
            self._tracer = trace.get_tracer(name=self._tracer_name)

        try:
            msg_template = record.msg
            attributes = {
                'code.filepath': record.pathname,
                'code.lineno': record.lineno,
                'code.function': record.funcName,
                'log.level': record.levelname,
                'log.logger': record.name,
                'log.message': self.format(record),
            }
            self._create_span(
                span_name=msg_template,
                attributes=attributes,
                exc_info=record.exc_info,
            )
        except RecursionError:  # See issue 36272
            raise
        except Exception:
            self.handleError(record)

    def _create_span(self,
                     span_name: str,
                     attributes: dict[str, AttributeValueType] = None,
                     exc_info: BaseException = None):
        start_time = time.time_ns()
        span = self._tracer.start_span(
            name=span_name,
            attributes=attributes,
            start_time=start_time,
        )
        if exc_info:
            span.record_exception(exception=exc_info, timestamp=start_time)
        span.end()
