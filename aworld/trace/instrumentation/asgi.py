from timeit import default_timer
from typing import Any, Awaitable, Callable
from functools import wraps
from aworld.metrics.context_manager import MetricContext
from aworld.trace.instrumentation.http_util import (
    collect_request_attributes_asgi,
    url_disabled,
    parser_host_port_url_from_asgi
)
from aworld.trace.base import Span, TraceProvider, TraceContext, Tracer, SpanType, get_tracer_provider
from aworld.trace.propagator import get_global_trace_propagator
from aworld.metrics.metric import MetricType
from aworld.metrics.template import MetricTemplate


def _wrapped_send(
    server_span: Span,
    server_span_name: str,
    scope: dict[str, Any],
    send: Callable[[dict[str, Any]], Awaitable[None]],
    attributes: dict[str],
):
    expecting_trailers = False

    @wraps(send)
    async def otel_send(message: dict[str, Any]):
        nonlocal expecting_trailers

        status_code = None
        if message["type"] == "http.response.start":
            status_code = message["status"]
        elif message["type"] == "websocket.send":
            status_code = 200

        # raw_headers = message.get("headers")
        # if raw_headers:
        server_span.set_attribute(
            "http.response.status_code", status_code)

        propagator = get_global_trace_propagator()
        if propagator:
            trace_context = TraceContext(
                trace_id=server_span.get_trace_id(),
                span_id=server_span.get_span_id()
            )
            propagator.inject(
                trace_context, ResponseCarrier(response_headers))

        content_length = asgi_getter.get(message, "content-length")
        if content_length:
            try:
                self.content_length_header = int(content_length[0])
            except ValueError:
                pass

        await send(message)

        # pylint: disable=too-many-boolean-expressions
        if (
            not expecting_trailers
            and message["type"] == "http.response.body"
            and not message.get("more_body", False)
        ) or (
            expecting_trailers
            and message["type"] == "http.response.trailers"
            and not message.get("more_trailers", False)
        ):
            server_span.end()

    return otel_send


class TraceMiddleware:
    """
    A ASGI Middleware for tracing requests and responses.
    """

    def __init__(
            self,
            app,
            excluded_urls=None,
            tracer_provider=None,
            server_request_hook: Callable = None,
            client_request_hook: Callable = None,
            client_response_hook: Callable = None,):
        self.app = app
        self.excluded_urls = excluded_urls
        self.tracer_provider = tracer_provider
        self.server_request_hook = server_request_hook
        self.client_request_hook = client_request_hook
        self.client_response_hook = client_response_hook
        self.tracer: Tracer = self.tracer_provider.get_tracer(
            "aworld.trace.instrumentation.asgi"
        )
        self.duration_histogram = MetricTemplate(
            type=MetricType.HISTOGRAM,
            name="asgi_request_duration_histogram",
            description="Duration of flask HTTP server requests."
        )

        self.active_requests_counter = MetricTemplate(
            type=MetricType.UPDOWNCOUNTER,
            name="asgi_active_request_counter",
            unit="1",
            description="Number of active HTTP server requests.",
        )

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ):
        start = default_timer()
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        _, _, url = parser_host_port_url_from_asgi(scope)
        if self.excluded_urls and url_disabled(url, self.excluded_urls):
            return await self.app(scope, receive, send)

        span_name = scope.get("method", "HTTP").strip(
        ).upper() + "_" + scope.get("path", "").strip()

        attributes = collect_request_attributes_asgi(scope)

        if scope["type"] == "http" and MetricContext.metric_initialized():
            MetricContext.inc(self.active_requests_counter, 1, attributes)
        try:
            with self.tracer.start_as_current_span(
                span_name, span_type=SpanType.SERVER, attributes=attributes
            ) as span:

                if callable(self.server_request_hook):
                    self.server_request_hook(scope)

                await self.app(scope, receive, send)
        finally:
            if scope["type"] == "http":
                target = _collect_target_attribute(scope)
                if target:
                    path, query = _parse_url_query(target)
                    _set_http_target(
                        attributes,
                        target,
                        path,
                        query,
                        self._sem_conv_opt_in_mode,
                    )
                duration_s = default_timer() - start

                if MetricContext.metric_initialized():
                    MetricContext.histogram_record(
                        self.duration_histogram,
                        duration_s,
                        attributes
                    )
                    MetricContext.inc(
                        self.active_requests_counter, -1, attributes)

                if target:
                    duration_attrs_old[SpanAttributes.HTTP_TARGET] = target
                duration_attrs_new = _parse_duration_attrs(
                    attributes, _StabilityMode.HTTP
                )
                if self.duration_histogram_old:
                    self.duration_histogram_old.record(
                        max(round(duration_s * 1000), 0), duration_attrs_old
                    )
                if self.duration_histogram_new:
                    self.duration_histogram_new.record(
                        max(duration_s, 0), duration_attrs_new
                    )
                self.active_requests_counter.add(
                    -1, active_requests_count_attrs
                )
                if self.content_length_header:
                    if self.server_response_size_histogram:
                        self.server_response_size_histogram.record(
                            self.content_length_header, duration_attrs_old
                        )
                    if self.server_response_body_size_histogram:
                        self.server_response_body_size_histogram.record(
                            self.content_length_header, duration_attrs_new
                        )

                request_size = asgi_getter.get(scope, "content-length")
                if request_size:
                    try:
                        request_size_amount = int(request_size[0])
                    except ValueError:
                        pass
                    else:
                        if self.server_request_size_histogram:
                            self.server_request_size_histogram.record(
                                request_size_amount, duration_attrs_old
                            )
                        if self.server_request_body_size_histogram:
                            self.server_request_body_size_histogram.record(
                                request_size_amount, duration_attrs_new
                            )
            if token:
                context.detach(token)
            if span.is_recording():
                span.end()
