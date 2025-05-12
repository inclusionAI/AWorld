import flask
from typing import Any
from time import time_ns
from timeit import default_timer
from aworld.trace.instrumentation.base import Instrumentor
from aworld.trace.base import TraceProvider
from aworld.metrics.metric import MetricProvider, MetricType
from aworld.metrics.template import MetricTemplate
from aworld.logs.util import logger
from aworld.trace.instrumentation.http_util import collect_request_attributes, url_disabled
from aworld.trace.propagator import get_global_trace_propagator
from aworld.metrics.context_manager import MetricContext

_ENVIRON_STARTTIME_KEY = "aworld-flask.starttime_key"
_ENVIRON_SPAN_KEY = "aworld-flask.span_key"


def _rewrapped_app(
    wsgi_app,
    active_requests_counter,
    duration_histogram,
    response_hook=None,
    excluded_urls=None,
):
    def _wrapped_app(wrapped_app_environ, start_response):
        # We want to measure the time for route matching, etc.
        # In theory, we could start the span here and use
        # update_name later but that API is "highly discouraged" so
        # we better avoid it.
        wrapped_app_environ[_ENVIRON_STARTTIME_KEY] = time_ns()
        start = default_timer()
        attributes = collect_request_attributes(wrapped_app_environ)

        MetricContext.inc(active_requests_counter, 1, attributes)

        request_route = None

        def _start_response(status, response_headers, *args, **kwargs):
            if flask.request and (
                excluded_urls is None
                or not url_disabled(flask.request.url, excluded_urls)
            ):
                nonlocal request_route
                request_route = flask.request.url_rule

                span = flask.request.environ.get(_ENVIRON_SPAN_KEY)

                propagator = get_global_trace_propagator()
                if propagator:
                    propagator.inject(
                        response_headers,
                        setter=otel_wsgi.default_response_propagation_setter,
                    )

                if span:
                    otel_wsgi.add_response_attributes(
                        span,
                        status,
                        response_headers,
                        attributes,
                        sem_conv_opt_in_mode,
                    )
                    if (
                        span.is_recording()
                        and span.kind == trace.SpanKind.SERVER
                    ):
                        custom_attributes = otel_wsgi.collect_custom_response_headers_attributes(
                            response_headers
                        )
                        if len(custom_attributes) > 0:
                            span.set_attributes(custom_attributes)
                else:
                    _logger.warning(
                        "Flask environ's OpenTelemetry span "
                        "missing at _start_response(%s)",
                        status,
                    )
                if response_hook is not None:
                    response_hook(span, status, response_headers)
            return start_response(status, response_headers, *args, **kwargs)

        result = wsgi_app(wrapped_app_environ, _start_response)
        duration_s = default_timer() - start
        if duration_histogram_old:
            duration_attrs_old = otel_wsgi._parse_duration_attrs(
                attributes, _StabilityMode.DEFAULT
            )

            if request_route:
                # http.target to be included in old semantic conventions
                duration_attrs_old[SpanAttributes.HTTP_TARGET] = str(
                    request_route
                )

            duration_histogram_old.record(
                max(round(duration_s * 1000), 0), duration_attrs_old
            )
        if duration_histogram_new:
            duration_attrs_new = otel_wsgi._parse_duration_attrs(
                attributes, _StabilityMode.HTTP
            )

            if request_route:
                duration_attrs_new[HTTP_ROUTE] = str(request_route)

            duration_histogram_new.record(
                max(duration_s, 0), duration_attrs_new
            )
        active_requests_counter.add(-1, active_requests_count_attrs)
        return result

    return _wrapped_app


class _InstrumentedFlask(flask.Flask):
    _excluded_urls = None
    _tracer_provider = None
    _request_hook = None
    _response_hook = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        trace_provider: TraceProvider = kwargs.get("trace_provider")
        tracer = trace_provider.get_tracer(
            "aworld.trace.instrumentation.flask")

        duration_histogram = MetricTemplate(
            type=MetricType.HISTOGRAM,
            name="flask_request_duration_histogram",
            description="Duration of flask HTTP server requests."
        )

        active_requests_counter = MetricTemplate(
            type=MetricType.UPDOWNCOUNTER,
            name="flask_active_request_counter",
            unit="1",
            description="Number of active HTTP server requests.",
        )

        self.wsgi_app = _rewrapped_app(
            self.wsgi_app,
            active_requests_counter,
            duration_histogram,
            _InstrumentedFlask._response_hook,
            excluded_urls=_InstrumentedFlask._excluded_urls
        )


class FlaskInstrumentor(Instrumentor):

    def _instrument(self, **kwargs: Any):
