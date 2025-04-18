import sys
import traceback
import time
import requests
from threading import Lock
from typing import Any, Iterator, Sequence
from contextvars import Token
from urllib.parse import urljoin
import opentelemetry.context as otlp_context_api
from opentelemetry.trace import SpanKind, set_span_in_context, get_current_span as get_current_otlp_span
from opentelemetry.sdk.trace import (
    ReadableSpan,
    SynchronousMultiSpanProcessor,
    Tracer as SDKTracer,
    Span as SDKSpan,
    TracerProvider as SDKTracerProvider
)
from opentelemetry.context import Context as OTLPContext
from opentelemetry.semconv.trace import SpanAttributes
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from aworld.trace.trace import (
    AttributeValueType,
    NoOpTracer,
    SpanType,
    TraceProvider,
    Tracer,
    Span,
    set_tracer_provider
)

from ..constants import ATTRIBUTES_MESSAGE_KEY
from typing import Optional
from typing import TYPE_CHECKING


class OTLPTraceProvider(TraceProvider):
    """A TraceProvider that wraps an existing `SDKTracerProvider`.
    This class provides a way to use a `SDKTracerProvider` as a `TraceProvider`.
    When the context manager is entered, it returns the `SDKTracerProvider` itself.
    When the context manager is exited, it calls `shutdown` on the `SDKTracerProvider`.
    Args:
        provider: The internal provider to wrap.
    """

    def __init__(self, provider: SDKTracerProvider, suppressed_scopes: Optional[set[str]] = None):
        self._provider: SDKTracerProvider = provider
        self._suppressed_scopes = set()
        if suppressed_scopes:
            self._suppressed_scopes.update(suppressed_scopes)
        self._lock: Lock = Lock()

    def get_tracer(
            self,
            name: str,
            version: Optional[str] = None
    ):
        with self._lock:
            if name in self._suppressed_scopes:
                return NoOpTracer()
            else:
                tracer = self._provider.get_tracer(instrumenting_module_name=name,
                                                   instrumenting_library_version=version)
                return OTLPTracer(tracer)

    def shutdown(self) -> None:
        with self._lock:
            if isinstance(self._provider, SDKTracerProvider):
                self._provider.shutdown()

    def force_flush(self, timeout: Optional[float] = None) -> bool:
        with self._lock:
            if isinstance(self._provider, SDKTracerProvider):
                return self._provider.force_flush(timeout)
            else:
                return False

    def get_current_span(self) -> Optional["Span"]:
        otlp_span = get_current_otlp_span()
        return OTLPSpan(otlp_span)


class OTLPTracer(Tracer):
    """A Tracer represents a collection of Spans.
    Args:
        tracer: The internal tracer to wrap.
    """

    def __init__(self, tracer: SDKTracer):
        self._tracer = tracer

    def start_span(
            self,
            name: str,
            span_type: SpanType = SpanType.INTERNAL,
            attributes: dict[str, AttributeValueType] = None,
            start_time: Optional[int] = None,
            record_exception: bool = True,
            set_status_on_exception: bool = True,
    ) -> "Span":
        start_time = start_time or time.time_ns()
        attributes = {**(attributes or {})}
        attributes.setdefault(ATTRIBUTES_MESSAGE_KEY, name)

        span_kind = self._convert_to_span_kind(span_type) if span_type else SpanKind.INTERNAL
        span = self._tracer.start_span(name=name,
                                       kind=span_kind,
                                       attributes=attributes,
                                       start_time=start_time,
                                       record_exception=record_exception,
                                       set_status_on_exception=set_status_on_exception)
        return OTLPSpan(span)

    def start_as_current_span(
            self,
            name: str,
            span_type: SpanType = SpanType.INTERNAL,
            attributes: dict[str, AttributeValueType] = None,
            start_time: Optional[int] = None,
            record_exception: bool = True,
            set_status_on_exception: bool = True,
            end_on_exit: bool = True,
    ) -> Iterator["Span"]:

        start_time = start_time or time.time_ns()
        attributes = {**(attributes or {})}
        attributes.setdefault(ATTRIBUTES_MESSAGE_KEY, name)
        span_kind = self._convert_to_span_kind(span_type) if span_type else SpanKind.INTERNAL

        with self._tracer.start_as_current_span(name=name,
                                                kind=span_kind,
                                                attributes=attributes,
                                                start_time=start_time,
                                                record_exception=record_exception,
                                                set_status_on_exception=set_status_on_exception,
                                                end_on_exit=end_on_exit) as span:
            yield OTLPSpan(span)

    def _convert_to_span_kind(self, span_type: SpanType) -> str:
        if span_type == SpanType.INTERNAL:
            return SpanKind.INTERNAL
        elif span_type == SpanType.CLIENT:
            return SpanKind.CLIENT
        elif span_type == SpanType.SERVER:
            return SpanKind.SERVER
        elif span_type == SpanType.PRODUCER:
            return SpanKind.PRODUCER
        elif span_type == SpanType.CONSUMER:
            return SpanKind.CONSUMER
        else:
            return SpanKind.INTERNAL


class OTLPSpan(Span, ReadableSpan):
    """A Span represents a single operation within a trace.
    """

    def __init__(self, span: SDKSpan):
        self._span = span
        self._token: Optional[Token[OTLPContext]] = None
        self._attach()
        self._add_to_open_spans()

    if not TYPE_CHECKING:  # pragma: no branch
        def __getattr__(self, name: str) -> Any:
            return getattr(self._span, name)

    def end(self, end_time: Optional[int] = None) -> None:
        self._remove_from_open_spans()
        end_time = end_time or time.time_ns()
        self._span.end(end_time=end_time)
        self._detach()

    def set_attribute(self, key: str, value: Any) -> None:
        self._span.set_attribute(key=key, value=value)

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        self._span.set_attributes(attributes=attributes)

    def is_recording(self) -> bool:
        return self._span.is_recording()

    def record_exception(
            self,
            exception: BaseException,
            attributes: dict[str, Any] = None,
            timestamp: Optional[int] = None,
            escaped: bool = False,
    ) -> None:
        timestamp = timestamp or time.time_ns()
        attributes = {**(attributes or {})}

        if exception is not sys.exc_info()[1]:
            # OTEL's record_exception uses `traceback.format_exc()` which is for the current exception,
            # ignoring the passed exception.
            # So we override the stacktrace attribute with the correct one.
            stacktrace = ''.join(traceback.format_exception(type(exception), exception, exception.__traceback__))
            attributes[SpanAttributes.EXCEPTION_STACKTRACE] = stacktrace

        self._span.record_exception(exception=exception,
                                    attributes=attributes,
                                    timestamp=timestamp,
                                    escaped=escaped)

    def get_trace_id(self) -> str:
        """Get the trace ID of the span.
        Returns:
            The trace ID of the span.
        """
        return f"{self._span.get_span_context().trace_id:032x}"

    def _attach(self):
        if self._token is not None:
            return
        self._token = otlp_context_api.attach(set_span_in_context(self._span))

    def _detach(self):
        if self._token is None:
            return
        otlp_context_api.detach(self._token)
        self._token = None


def configure_otlp_provider(
        backends: Sequence[str] = None,
        base_url: str = None,
        write_token: str = None,
        **kwargs
) -> None:
    """Configure the OTLP provider.
    Args:
        backend: The backend to use.
        write_token: The write token to use.
        **kwargs: Additional keyword arguments to pass to the provider.
    """
    backends = backends or ["logfire"]
    processor = SynchronousMultiSpanProcessor()
    for backend in backends:
        if backend == "logfire":
            span_exporter = _configure_logfire_exporter(write_token=write_token, base_url=base_url, **kwargs)
            processor.add_span_processor(BatchSpanProcessor(span_exporter))
        elif backend == "console":
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            processor.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    set_tracer_provider(OTLPTraceProvider(SDKTracerProvider(active_span_processor=processor)))


def _configure_logfire_exporter(write_token: str, base_url: str = None) -> None:
    """Configure the Logfire exporter.
    Args:
        write_token: The write token to use.
        base_url: The base URL to use.
        **kwargs: Additional keyword arguments to pass to the exporter.
    """
    from opentelemetry.exporter.otlp.proto.http import Compression
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    base_url = base_url or "https://logfire-us.pydantic.dev"
    headers = {'User-Agent': f'logfire/3.14.0', 'Authorization': write_token}
    print("=========headers", headers)
    session = requests.Session()
    session.headers.update(headers)
    return OTLPSpanExporter(
        endpoint=urljoin(base_url, '/v1/traces'),
        session=session,
        compression=Compression.Gzip,
    )
