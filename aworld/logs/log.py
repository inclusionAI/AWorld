import time
import sys
import os
from logging import StreamHandler
from abc import ABC
from logging import Logger, NOTSET, LogRecord, Filter, Formatter, Handler
from typing import Optional, Union

from aworld.trace.base import get_tracer_provider_silent, Tracer, AttributeValueType

TRACE_LOG_FORMAT = '%(asctime)s - [%(trace_id)s] - [%(span_id)s] - %(name)s - %(levelname)s - %(message)s'
SPECIAL_TRACE_LOG_FORMAT = '%(asctime)s - [trace_%(trace_id)s] - [%(span_id)s] - %(name)s - %(levelname)s - %(message)s'


class LoggerProvider(ABC):
    """A logger provider is a factory for loggers."""


_GLOBAL_LOG_PROVIDER: Optional[LoggerProvider] = None


def set_log_provider(provider: str = "otlp",
                     backend: str = "logfire",
                     base_url: str = None,
                     write_token: str = None,
                     **kwargs):
    """Set the global log provider."""

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


def instrument_logging(logger: Logger, level: Union[int, str] = NOTSET) -> None:
    """Instrument the logger."""
    pass
    # for handler in logger.handlers:
    #     if not any(isinstance(filter, TraceLoggingFilter) for filter in handler.filters):
    #         handler.setFormatter(Formatter(TRACE_LOG_FORMAT))
    #         handler.addFilter(TraceLoggingFilter())
    #
    # if not logger.handlers:
    #     print("No handlers found, adding a StreamHandler. logger=", logger.name)
    #     handler = StreamHandler()
    #     handler.setFormatter(Formatter(SPECIAL_TRACE_LOG_FORMAT))
    #     handler.addFilter(TraceLoggingFilter())
    #     logger.addHandler(handler)
    # else:
    #     for handler in logger.handlers:
    #         if not any(isinstance(filter, TraceLoggingFilter) for filter in handler.filters):
    #             handler.setFormatter(Formatter(SPECIAL_TRACE_LOG_FORMAT))
    #             handler.addFilter(TraceLoggingFilter())
    # logger.propagate = False
    # logger.addHandler(TraceLogginHandler(level))


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
    @staticmethod
    def strip_color(text: str) -> str:
        """Remove ANSI color codes from text"""
        import re
        return re.sub(r'\033\[[0-9;]*m', '', text)

    def __init__(self,
                 level: Union[int, str] = NOTSET,
                 tracer_name: str = "aworld.log") -> None:
        """Initialize the handler."""
        super().__init__(level=level)
        self._tracer_name = tracer_name
        self._tracer: Tracer = None

    def emit(self, record: LogRecord) -> None:
        """Emit a record."""
        trace = get_tracer_provider_silent()
        if not trace or not trace.get_current_span() or not trace.get_current_span().is_recording():
            return

        if not self._tracer:
            self._tracer = trace.get_tracer(name=self._tracer_name)

        try:
            f = sys._getframe()
            while f:
                if 'logging/__init__.py' in f.f_code.co_filename or \
                        f.f_code.co_filename.startswith(os.path.dirname(__file__)):
                    f = f.f_back
                else:
                    break

            origin_msg = record.msg
            raw_msg = None
            if f:
                try:
                    import linecache
                    line = linecache.getline(f.f_code.co_filename, f.f_lineno)
                    if 'logger.' in line:
                        raw_msg = line.split('logger.', 1)[1].split(
                            '(', 1)[1].split(')', 1)[0].strip()
                except:
                    pass
            record.msg = self.strip_color(record.msg)
            msg_template = raw_msg if raw_msg else record.msg

            if len(msg_template) > 255:
                msg_template = msg_template[:255] + '...'

            attributes = {
                'code.filepath': f.f_code.co_filename if f else record.pathname,
                'code.lineno': f.f_lineno if f else record.lineno,
                'code.function': f.f_code.co_name if f else record.funcName,
                'log.level': record.levelname,
                'log.logger': record.name,
                'log.message': self.format(record),
            }
            record.msg = origin_msg
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
