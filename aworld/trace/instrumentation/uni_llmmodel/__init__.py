import wrapt
import time
import inspect
import traceback
from typing import Collection, Any, Union
from aworld.trace.instrumentation import Instrumentor
from aworld.trace.base import (
    Tracer,
    SpanType,
    get_tracer_provider_silent
)
from aworld.trace.constants import ATTRIBUTES_MESSAGE_RUN_TYPE_KEY, RunType
from aworld.trace.instrumentation.llm_metrics import (
    record_exception_metric,
    record_chat_response_metric,
    record_streaming_time_to_first_token,
    record_streaming_time_to_generate
)
from aworld.trace.instrumentation.uni_llmmodel.model_response_parse import (
    accumulate_stream_response,
    get_common_attributes_from_response,
    record_stream_token_usage,
    parse_response_message,
    response_to_dic,
    handle_request
)
from aworld.trace.instrumentation.openai.inout_parse import run_async

from aworld.metrics.template import MetricTemplate
from aworld.metrics.context_manager import MetricContext
from aworld.metrics.metric import MetricType
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse
from aworld.logs.util import logger


def _completion_wrapper(tracer: Tracer):

    @wrapt.decorator
    def wrapper(wrapped, instance, args, kwargs):
        model_name = instance.provider.model_name
        if not model_name:
            model_name = "LLMModel"
        span_attributes = {}
        span_attributes[ATTRIBUTES_MESSAGE_RUN_TYPE_KEY] = RunType.LLM.value

        span = tracer.start_span(
            name=model_name, span_type=SpanType.CLIENT, attributes=span_attributes)

        run_async(handle_request(span, kwargs, instance))
        start_time = time.time()
        try:
            response = wrapped(*args, **kwargs)
        except Exception as e:
            record_exception(span=span,
                             start_time=start_time,
                             exception=e
                             )
            span.end()
            raise e

        if (is_streaming_response(response)):
            return WrappedGeneratorResponse(span=span,
                                            response=response,
                                            instance=instance,
                                            start_time=start_time,
                                            request_kwargs=kwargs
                                            )
        record_completion(span=span,
                          start_time=start_time,
                          response=response,
                          request_kwargs=kwargs,
                          instance=instance,
                          is_async=False
                          )
        span.end()
        return response

    return wrapper


def _acompletion_class_wrapper(tracer: Tracer):

    async def awrapper(wrapped, instance, args, kwargs):
        model_name = instance.provider.model_name
        if not model_name:
            model_name = "LLMModel"
        span_attributes = {}
        span_attributes[ATTRIBUTES_MESSAGE_RUN_TYPE_KEY] = RunType.LLM.value

        span = tracer.start_span(
            name=model_name, span_type=SpanType.CLIENT, attributes=span_attributes)

        await handle_request(span, kwargs, instance)
        start_time = time.time()
        try:
            response = await wrapped(*args, **kwargs)
        except Exception as e:
            record_exception(span=span,
                             start_time=start_time,
                             exception=e
                             )
            span.end()
            raise e

        if (is_streaming_response(response)):
            return WrappedGeneratorResponse(span=span,
                                            response=response,
                                            instance=instance,
                                            start_time=start_time,
                                            request_kwargs=kwargs
                                            )
        record_completion(span=span,
                          start_time=start_time,
                          response=response,
                          request_kwargs=kwargs,
                          instance=instance,
                          is_async=True
                          )
        span.end()
        return response

    return awrapper


async def _acompletion_instance_wrapper(tracer: Tracer):

    @wrapt.decorator
    async def _awrapper(wrapped, instance, args, kwargs):
        wrapper_func = _acompletion_class_wrapper(tracer)
        return await wrapper_func(wrapped, instance, args, kwargs)

    return _awrapper


def is_streaming_response(response):
    return inspect.isgenerator(response)


def record_exception(span, start_time, exception):
    '''
    record openai chat exception to trace and metrics
    '''
    try:
        duration = time.time() - start_time if "start_time" in locals() else 0
        if span.is_recording:
            span.record_exception(exception=exception)
        record_exception_metric(exception=exception, duration=duration)
    except Exception as e:
        logger.warning(f"openai instrument record exception error.{e}")


def record_completion(span,
                      start_time,
                      response,
                      request_kwargs,
                      instance,
                      is_async):
    '''
    Record chat completion to trace and metrics
    '''
    duration = time.time() - start_time if "start_time" in locals() else 0
    response_dict = response_to_dic(response)
    attributes = get_common_attributes_from_response(instance, is_async, False)
    usage = response_dict.get("usage")
    content = response_dict.get("content", "")
    tool_calls = response_dict.get("tool_calls")
    prompt_tokens = -1
    completion_tokens = -1
    total_tokens = -1
    if usage:
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

    span_attributes = {
        **attributes,
        "llm.prompt_tokens": prompt_tokens,
        "llm.completion_tokens": completion_tokens,
        "llm.total_tokens": total_tokens,
        "llm.duration": duration,
        "llm.content": content
    }
    span_attributes.update(parse_response_message(tool_calls))
    span.set_attributes(span_attributes)
    record_chat_response_metric(attributes=attributes,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                duration=duration
                                )


class WrappedGeneratorResponse(wrapt.ObjectProxy):

    def __init__(
        self,
        span,
        response,
        instance=None,
        start_time=None,
        request_kwargs=None
    ):
        super().__init__(response)
        self._span = span
        self._instance = instance
        self._start_time = start_time
        self._complete_response = {"choices": [], "model": ""}
        self._first_token_recorded = False
        self._time_of_first_token = None
        self._request_kwargs = request_kwargs

    def __iter__(self):
        return self

    def __aiter__(self):
        return self

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
        except Exception as e:
            if isinstance(e, StopIteration):
                self._close_span(False)
            raise e
        else:
            self._process_stream_chunk(chunk, False)
            return chunk

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
        except Exception as e:
            if isinstance(e, StopAsyncIteration):
                self._close_span(True)
            raise e
        else:
            self._process_stream_chunk(chunk, True)
            return chunk

    def _process_stream_chunk(self, chunk: ModelResponse, is_async):
        accumulate_stream_response(chunk, self._complete_response)

        if not self._first_token_recorded:
            self._time_of_first_token = time.time()
            duration = self._time_of_first_token - self._start_time
            attribute = get_common_attributes_from_response(
                self._instance, is_async, True)
            record_streaming_time_to_first_token(duration, attribute)
            self._first_token_recorded = True

    def _close_span(self, is_async):
        duration = None
        first_token_duration = None
        first_token_to_generate_duration = None
        if self._start_time and isinstance(self._start_time, (float, int)):
            duration = time.time() - self._start_time
        if self._time_of_first_token and self._start_time and isinstance(self._start_time, (float, int)):
            first_token_duration = self._time_of_first_token - self._start_time
            first_token_to_generate_duration = time.time() - self._time_of_first_token

        prompt_usage, completion_usage = record_stream_token_usage(
            self._complete_response, self._request_kwargs)

        attributes = get_common_attributes_from_response(
            self._instance, is_async, True)

        choices = self._complete_response.get("choices")
        span_attributes = {
            **attributes,
            "llm.prompt_tokens": prompt_usage,
            "llm.completion_tokens": completion_usage,
            "llm.duration": duration,
            "llm.first_token_duration": first_token_duration
        }
        span_attributes.update(parse_response_message(choices))

        self._span.set_attributes(span_attributes)
        record_chat_response_metric(attributes=attributes,
                                    prompt_tokens=prompt_usage,
                                    completion_tokens=completion_usage,
                                    duration=duration,
                                    choices=choices
                                    )
        record_streaming_time_to_generate(
            first_token_to_generate_duration, attributes)

        self._span.end()


class LLMModelInstrumentor(Instrumentor):

    def instrumentation_dependencies(self) -> Collection[str]:
        return ()

    def _instrument(self, **kwargs):
        tracer_provider = get_tracer_provider_silent()
        if not tracer_provider:
            return
        tracer = tracer_provider.get_tracer(
            "aworld.trace.instrumentation.llmmodel")

        wrapt.wrap_function_wrapper(
            "aworld.models.llm",
            "LLMModel.completion",
            _completion_wrapper(tracer=tracer)
        )

        wrapt.wrap_function_wrapper(
            "aworld.models.llm",
            "LLMModel.stream_completion",
            _completion_wrapper(tracer=tracer)
        )
        wrapt.wrap_function_wrapper(
            "aworld.models.llm",
            "LLMModel.acompletion",
            _acompletion_class_wrapper(tracer)
        )

        wrapt.wrap_function_wrapper(
            "aworld.models.llm",
            "LLMModel.astream_completion",
            _acompletion_class_wrapper(tracer)
        )

    def _uninstrument(self, **kwargs: Any):
        pass


def wrap_llmmodel(client: LLMModel):
    try:
        tracer_provider = get_tracer_provider_silent()
        if not tracer_provider:
            return client
        tracer = tracer_provider.get_tracer(
            "aworld.trace.instrumentation.llmmodel")

        wrapper = _completion_wrapper(tracer)
        awrapper = _acompletion_instance_wrapper(tracer)
        client.completion = wrapper(client.completion)
        client.stream_completion = wrapper(client.stream_completion)
        client.acompletion = awrapper(client.acompletion)
        client.astream_completion = awrapper(client.astream_completion)
    except Exception:
        logger.warning(traceback.format_exc())

    return client
