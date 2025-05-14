import functools
from requests.sessions import Session
from requests.models import PreparedRequest, Response
from requests.structures import CaseInsensitiveDict
from typing import Collection, Any, Callable
from aworld.trace.base import Span, TraceProvider, TraceContext, Tracer, SpanType, get_tracer_provider
from aworld.trace.instrumentation import Instrumentor
from aworld.metrics.metric import MetricType
from aworld.metrics.template import MetricTemplate
from aworld.trace.instrumentation.http_util import (
    collect_request_attributes,
    url_disabled
)


def _wrapped_send(
    tracer: Tracer = None,
    excluded_urls=None,
    request_hook: Callable = None,
    response_hook: Callable = None,
):

    oringinal_send = Session.send

    @functools.wraps(oringinal_send)
    def instrumented_send(
        self: Session, request: PreparedRequest, **kwargs: Any
    ):
        if excluded_urls and url_disabled(request.url, excluded_urls):
            return oringinal_send(self, request, **kwargs)

        def get_or_create_headers():
            request.headers = (
                request.headers
                if request.headers is not None
                else CaseInsensitiveDict()
            )
            return request.headers

        if not is_http_instrumentation_enabled():
            return wrapped_send(self, request, **kwargs)

        # See
        # https://github.com/open-telemetry/semantic-conventions/blob/main/docs/http/http-spans.md#http-client
        method = request.method
        span_name = get_default_span_name(method)

        url = remove_url_credentials(request.url)

        span_attributes = {}
        _set_http_method(
            span_attributes,
            method,
            sanitize_method(method),
            sem_conv_opt_in_mode,
        )
        _set_http_url(span_attributes, url, sem_conv_opt_in_mode)

        metric_labels = {}
        _set_http_method(
            metric_labels,
            method,
            sanitize_method(method),
            sem_conv_opt_in_mode,
        )

        try:
            parsed_url = urlparse(url)
            if parsed_url.scheme:
                if _report_old(sem_conv_opt_in_mode):
                    # TODO: Support opt-in for url.scheme in new semconv
                    _set_http_scheme(
                        metric_labels, parsed_url.scheme, sem_conv_opt_in_mode
                    )
            if parsed_url.hostname:
                _set_http_host_client(
                    metric_labels, parsed_url.hostname, sem_conv_opt_in_mode
                )
                _set_http_net_peer_name_client(
                    metric_labels, parsed_url.hostname, sem_conv_opt_in_mode
                )
                if _report_new(sem_conv_opt_in_mode):
                    _set_http_host_client(
                        span_attributes,
                        parsed_url.hostname,
                        sem_conv_opt_in_mode,
                    )
                    # Use semconv library when available
                    span_attributes[NETWORK_PEER_ADDRESS] = parsed_url.hostname
            if parsed_url.port:
                _set_http_peer_port_client(
                    metric_labels, parsed_url.port, sem_conv_opt_in_mode
                )
                if _report_new(sem_conv_opt_in_mode):
                    _set_http_peer_port_client(
                        span_attributes, parsed_url.port, sem_conv_opt_in_mode
                    )
                    # Use semconv library when available
                    span_attributes[NETWORK_PEER_PORT] = parsed_url.port
        except ValueError:
            pass

        with tracer.start_as_current_span(
            span_name, kind=SpanKind.CLIENT, attributes=span_attributes
        ) as span, set_ip_on_next_http_connection(span):
            exception = None
            if callable(request_hook):
                request_hook(span, request)

            headers = get_or_create_headers()
            inject(headers)

            with suppress_http_instrumentation():
                start_time = default_timer()
                try:
                    result = wrapped_send(
                        self, request, **kwargs
                    )  # *** PROCEED
                except Exception as exc:  # pylint: disable=W0703
                    exception = exc
                    result = getattr(exc, "response", None)
                finally:
                    elapsed_time = max(default_timer() - start_time, 0)

            if isinstance(result, Response):
                span_attributes = {}
                _set_http_status_code_attribute(
                    span,
                    result.status_code,
                    metric_labels,
                    sem_conv_opt_in_mode,
                )

                if result.raw is not None:
                    version = getattr(result.raw, "version", None)
                    if version:
                        # Only HTTP/1 is supported by requests
                        version_text = "1.1" if version == 11 else "1.0"
                        _set_http_network_protocol_version(
                            metric_labels, version_text, sem_conv_opt_in_mode
                        )
                        if _report_new(sem_conv_opt_in_mode):
                            _set_http_network_protocol_version(
                                span_attributes,
                                version_text,
                                sem_conv_opt_in_mode,
                            )
                for key, val in span_attributes.items():
                    span.set_attribute(key, val)

                if callable(response_hook):
                    response_hook(span, request, result)

            if exception is not None and _report_new(sem_conv_opt_in_mode):
                span.set_attribute(ERROR_TYPE, type(exception).__qualname__)
                metric_labels[ERROR_TYPE] = type(exception).__qualname__

            if duration_histogram_old is not None:
                duration_attrs_old = _filter_semconv_duration_attrs(
                    metric_labels,
                    _client_duration_attrs_old,
                    _client_duration_attrs_new,
                    _StabilityMode.DEFAULT,
                )
                duration_histogram_old.record(
                    max(round(elapsed_time * 1000), 0),
                    attributes=duration_attrs_old,
                )
            if duration_histogram_new is not None:
                duration_attrs_new = _filter_semconv_duration_attrs(
                    metric_labels,
                    _client_duration_attrs_old,
                    _client_duration_attrs_new,
                    _StabilityMode.HTTP,
                )
                duration_histogram_new.record(
                    elapsed_time, attributes=duration_attrs_new
                )

            if exception is not None:
                raise exception.with_traceback(exception.__traceback__)

        return result


class _InstrumentedSession(Session):
    """
    An instrumented requests.Session class.
    """
    _excluded_urls = None
    _tracer_provider: TraceProvider = None
    _request_hook = None
    _response_hook = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        tracer = self._tracer_provider.get_tracer(
            "aworld.trace.instrumentation.requests")
        excluded_urls = kwargs.get("excluded_urls")

        duration_histogram = MetricTemplate(
            type=MetricType.HISTOGRAM,
            name="client_request_duration_histogram",
            unit="s",
            description="Duration of  HTTP client requests."
        )


class RequestsInstrumentor(Instrumentor):
    """
    An instrumentor for the requests module.
    """

    def instrumentation_dependencies(self) -> Collection[str]:
        return ["requests"]

    def _instrument(self, **kwargs):
        """
        Instruments the requests module.
        """
