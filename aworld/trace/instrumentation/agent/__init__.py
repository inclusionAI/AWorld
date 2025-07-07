import wrapt
import time
import aworld.trace.constants as trace_constants
from typing import Collection, Any
from aworld.trace.instrumentation import Instrumentor
from aworld.trace.instrumentation import semconv
from aworld.trace.base import (
    Tracer,
    SpanType,
    get_tracer_provider_silent
)
from aworld.logs.util import logger
from aworld.metrics.context_manager import MetricContext
from aworld.metrics.template import MetricTemplate
from aworld.metrics.metric import MetricType

agent_duration_histogram = MetricTemplate(
    type=MetricType.HISTOGRAM,
    name="agent_run_duration",
    unit="s",
    description="Agent run duration",
)

agent_run_counter = MetricTemplate(
    type=MetricType.COUNTER,
    name="agent_run_counter",
    unit="time",
    description="Number of agent run or async run",
)

agent_usage_histogram = MetricTemplate(
    type=MetricType.HISTOGRAM,
    name="agent_token_usage",
    unit="token",
    description="Agent token usage"
)


def _end_span(span):
    if span:
        span.end()


def _record_metric(duration, attributes, exception=None):
    if MetricContext.metric_initialized():
        MetricContext.histogram_record(agent_duration_histogram, duration, labels=attributes)
        if exception:
            run_counter_attr = {
                semconv.AGENT_RUN_SUCCESS: "0",
                "error.type": exception.__class__.__name__,
                **attributes
            }
        else:
            run_counter_attr = {
                semconv.AGENT_RUN_SUCCESS: "1",
                **attributes
            }
        MetricContext.count(agent_run_counter, 1, labels=run_counter_attr)


def _record_exception(span, start_time, exception, attributes):
    try:
        duration = time.time() - start_time if "start_time" in locals() else 0
        if span.is_recording:
            span.record_exception(exception=exception)
        _record_metric(duration, attributes, exception)
    except Exception as e:
        logger.warning(f"agent instrument record exception error.{e}")


def _record_response(instance,
                     start_time,
                     response,
                     attributes):
    try:
        duration = time.time() - start_time if "start_time" in locals() else 0
        _record_metric(duration, attributes)
        if instance and instance.agent_context and instance.agent_context.llm_output and MetricContext.metric_initialized():
            usage = instance.agent_context.llm_output.usage
            for usage_type in ["completion_tokens", "prompt_tokens", "total_tokens"]:
                if usage and usage.get(usage_type):
                    labels = {
                        **attributes,
                        semconv.AGENT_USAGE_TYPE: usage_type
                    }
                    MetricContext.histogram_record(
                        agent_usage_histogram,
                        usage.get(usage_type),
                        labels=labels
                    )
    except Exception as e:
        logger.warning(f"agent instrument record response error.{e}")


def _async_run_class_wrapper(tracer: Tracer):
    async def _async_run_class_wrapper(wrapped, instance, args, kwargs):
        span = None
        attributes = {
            semconv.AGENT_ID: instance.id(),
            semconv.AGENT_NAME: instance.name()
        }
        if tracer:
            span = tracer.start_span(
                name=trace_constants.SPAN_NAME_PREFIX_AGENT + "async_run",
                span_type=SpanType.SERVER,
                attributes=attributes
            )
        start_time = time.time()
        try:
            response = await wrapped(*args, **kwargs)
            _record_response(instance, start_time, response, attributes)
        except Exception as e:
            _record_exception(span=span,
                              start_time=start_time,
                              exception=e,
                              attributes=attributes
                              )
            _end_span(span)
            raise e
        _end_span(span)
        return response
    return _async_run_class_wrapper


class AgentInstrumentor(Instrumentor):

    def instrumentation_dependencies(self) -> Collection[str]:
        return ()

    def _instrument(self, **kwargs):
        agent_trace_enabled = kwargs.get("agent_trace_enabled", False)
        tracer_provider = get_tracer_provider_silent()
        tracer = None
        if tracer_provider and agent_trace_enabled:
            tracer = tracer_provider.get_tracer(
                "aworld.trace.instrumentation.agent")

        wrapt.wrap_function_wrapper(
            "aworld.core.agent.base",
            "BaseAgent.async_run",
            _async_run_class_wrapper(tracer=tracer)
        )

    def _uninstrument(self, **kwargs: Any):
        pass
