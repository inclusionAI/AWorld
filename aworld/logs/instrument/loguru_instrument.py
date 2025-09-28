# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time
from typing import Optional
from loguru import logger as base_logger
from aworld.trace.base import get_tracer_provider_silent, Tracer

BASE_LOGGER_INSTRUMENTED = False


class LoguruTraceProcessor:
    """
    A processor for Loguru that adds trace information to log records.
    """

    def __init__(self, tracer_name: str = "aworld.loguru"):
        self._tracer_name = tracer_name
        self._tracer: Optional[Tracer] = None

    def process(self, record):
        """Process a loguru record to add trace information."""
        if 'extra' not in record:
            record['extra'] = {}
        record['extra'].setdefault('trace_id', '')
        record['extra'].setdefault('span_id', '')
        record['extra'].setdefault('name', self._tracer_name)

        if self._tracer is None:
            self._tracer = get_tracer_provider_silent()

        if self._tracer:
            try:
                span = self._tracer.get_current_span()
                if span:
                    record['extra']['trace_id'] = span.get_trace_id()
                    record['extra']['span_id'] = span.get_span_id()
            except Exception as e:
                print(f"Error in LoguruTraceProcessor.process: {e}")
                pass

        return record


class LoguruTraceInstrument:
    """
    A class that creates trace spans from loguru log records.
    """

    def __init__(self, tracer_name: str = "aworld.loguru", logger_tags: list = None):
        self._tracer_name = tracer_name
        self._tracer: Optional[Tracer] = None
        self._logger_tags = logger_tags or []

    def process(self, record):
        """Process a loguru record to create a trace span."""

        current_tag = record['extra'].get('name')
        if not current_tag or current_tag not in self._logger_tags:
            return record

        if self._tracer is None:
            provider = get_tracer_provider_silent()
            if provider is not None:
                self._tracer = provider.get_tracer(self._tracer_name)

        if self._tracer is not None:
            try:
                attributes = {
                    'log.level': record['level'].name,
                    'log.message': record['message'],
                    'log.logger': record['name'] if 'name' in record else '',
                    'log.module': record['module'] if 'module' in record else '',
                    'log.line': record['line'] if 'line' in record else 0,
                }

                if 'extra' in record and isinstance(record['extra'], dict):
                    if 'trace_id' in record['extra']:
                        attributes['trace_id'] = record['extra']['trace_id']
                    if 'span_id' in record['extra']:
                        attributes['span_id'] = record['extra']['span_id']

                start_time = time.time_ns()
                span = self._tracer.start_span(
                    name=f"log.{record['level'].name.lower()}",
                    attributes=attributes,
                    start_time=start_time,
                )

                if 'exception' in record and record['exception']:
                    span.record_exception(
                        exception=record['exception'],
                        timestamp=start_time
                    )
                span.end()
            except Exception:
                pass

        return record


trace_processor = LoguruTraceProcessor()


def get_trace_formatter():
    """Get a loguru formatter that includes trace information."""
    def _formatter(record):
        trace_processor.process(record)
        return "[{extra[trace_id]}] | <black>{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {extra[name]} PID: {process}, TID:{thread} |</black> <bold>{name}.{function}:{line}</bold> - \n<level>{message}</level> {exception}\n"
    return _formatter


def instrument_loguru_base_logger(level: str = None):
    """
    Instrument the base loguru logger to add trace information to log records.

    Args:
        level: The minimum log level to instrument
    """
    global BASE_LOGGER_INSTRUMENTED
    if BASE_LOGGER_INSTRUMENTED:
        return
    _add_trace_formatter(base_logger, level)
    BASE_LOGGER_INSTRUMENTED = True


def _get_handlers(logger):
    handlers = []
    if hasattr(logger, "_core"):
        core = getattr(logger, "_core")
        if hasattr(core, "handlers"):
            handlers_attr = getattr(core, "handlers")
            if isinstance(handlers_attr, dict):
                handlers = list(handlers_attr.values())
            elif hasattr(handlers_attr, "values"):
                try:
                    handlers = list(handlers_attr.values())
                except Exception:
                    pass
    return handlers


def _add_trace_formatter(logger, level: str = None):
    handlers = _get_handlers(logger)
    for handler in handlers:
        logger.remove(handler._id)
        logger.add(
            handler._sink,
            level=level if level is not None else handler._levelno,
            format=get_trace_formatter(),
            filter=handler._filter,
            enqueue=getattr(handler, '_enqueue', False),
            backtrace=getattr(handler, '_backtrace', False),
            diagnose=getattr(handler, '_diagnose', False)
        )


def instrument_loguru(logger_tags, level: str = None):
    """
    Instrument a loguru logger to add trace information to log records and create trace spans.

    Args:
        logger: The loguru logger to instrument. If None, only update format for all loguru loggers.
        level: The minimum log level to instrument
    """
    span_instrument = LoguruTraceInstrument(logger_tags=logger_tags)
    if not level:
        handlers = _get_handlers(base_logger)
        if handlers:
            level = handlers[0]._levelno
        else:
            level = "INFO"
    base_logger.add(
        lambda message: span_instrument.process(trace_processor.process(message.record)),
        level=level,
        format=get_trace_formatter(),
        filter=lambda record: True,
        enqueue=True,
        backtrace=True,
        diagnose=True
    )
    base_logger.info(f"instrument loguru logger {logger_tags} with level {level}")
