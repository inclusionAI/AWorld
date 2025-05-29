from re import A
import time
import wrapt
import openai
import traceback
from typing import Collection, Any, Union
from aworld.trace.instrumentation import Instrumentor
from aworld.trace.base import (
    Tracer,
    SpanType,
    get_tracer_provider_silent
)
from aworld.trace.constants import ATTRIBUTES_MESSAGE_RUN_TYPE_KEY, RunType
from aworld.trace.instrumentation.openai.inout_parse import (
    run_async,
    handle_openai_request,
    is_streaming_response,
    record_stream_response_chunk,
    parse_openai_response,
    record_stream_token_usage,
    model_as_dict,
    parse_response_message,
)
from aworld.metrics.template import MetricTemplate
from aworld.metrics.context_manager import MetricContext
from aworld.metrics.metric import MetricType
from aworld.logs.util import logger


def _chat_wrapper(tracer: Tracer,
                  tokens_usage_histogram: MetricTemplate = None,
                  chat_choice_counter: MetricTemplate = None,
                  duration_histogram: MetricTemplate = None,
                  chat_exception_counter: MetricTemplate = None,
                  streaming_time_to_first_token_histogram: MetricTemplate = None,
                  streaming_time_to_generate_histogram: MetricTemplate = None
                  ):

    @wrapt.decorator
    def wrapper(wrapped, instance, args, kwargs):
        model_name = kwargs.get("model", "")
        if not model_name:
            model_name = "OpenAI"
        span_attributes = {}
        span_attributes[ATTRIBUTES_MESSAGE_RUN_TYPE_KEY] = RunType.LLM.value

        span = tracer.start_span(
            name=model_name, span_type=SpanType.CLIENT, attributes=span_attributes)

        run_async(handle_openai_request(span, kwargs, instance))
        start_time = time.time()
        try:
            response = wrapped(*args, **kwargs)
        except Exception as e:
            record_exception(span=span,
                             start_time=start_time,
                             exception=e,
                             duration_histogram=duration_histogram,
                             chat_exception_counter=chat_exception_counter
                             )
            span.end()
            raise e

        if is_streaming_response(response):
            return WrappedStreamResponse(span=span,
                                         response=response,
                                         instance=instance,
                                         start_time=start_time,
                                         tokens_usage_histogram=tokens_usage_histogram,
                                         chat_choice_counter=chat_choice_counter,
                                         duration_histogram=duration_histogram,
                                         streaming_time_to_first_token_histogram=streaming_time_to_first_token_histogram,
                                         streaming_time_to_generate_histogram=streaming_time_to_generate_histogram,
                                         request_kwargs=kwargs
                                         )

        record_completion(span=span,
                          start_time=start_time,
                          response=response,
                          request_kwargs=kwargs,
                          instance=instance,
                          tokens_usage_histogram=tokens_usage_histogram,
                          duration_histogram=duration_histogram,
                          chat_choice_counter=chat_choice_counter
                          )
        span.end()
        return response

    return wrapper


def _achat_wrapper(tracer: Tracer,
                   tokens_usage_histogram: MetricTemplate = None,
                   chat_choice_counter: MetricTemplate = None,
                   duration_histogram: MetricTemplate = None,
                   chat_exception_counter: MetricTemplate = None,
                   streaming_time_to_first_token_histogram: MetricTemplate = None,
                   streaming_time_to_generate_histogram: MetricTemplate = None
                   ):

    @wrapt.decorator
    async def awrapper(wrapped, instance, args, kwargs):
        model_name = kwargs.get("model", "")
        if not model_name:
            model_name = "OpenAI"
        span_attributes = {}
        span_attributes[ATTRIBUTES_MESSAGE_RUN_TYPE_KEY] = RunType.LLM.value

        span = tracer.start_span(
            name=model_name, span_type=SpanType.CLIENT, attributes=span_attributes)

        await handle_openai_request(span, kwargs, instance)
        start_time = time.time()
        try:
            response = await wrapped(*args, **kwargs)
        except Exception as e:
            record_exception(span=span,
                             start_time=start_time,
                             exception=e,
                             duration_histogram=duration_histogram,
                             chat_exception_counter=chat_exception_counter
                             )
            span.end()
            raise e

        if is_streaming_response(response):
            return WrappedStreamResponse(span=span,
                                         response=response,
                                         instance=instance,
                                         start_time=start_time,
                                         tokens_usage_histogram=tokens_usage_histogram,
                                         chat_choice_counter=chat_choice_counter,
                                         duration_histogram=duration_histogram,
                                         streaming_time_to_first_token_histogram=streaming_time_to_first_token_histogram,
                                         streaming_time_to_generate_histogram=streaming_time_to_generate_histogram,
                                         request_kwargs=kwargs
                                         )
        record_completion(span=span,
                          start_time=start_time,
                          response=response,
                          request_kwargs=kwargs,
                          instance=instance,
                          tokens_usage_histogram=tokens_usage_histogram,
                          duration_histogram=duration_histogram,
                          chat_choice_counter=chat_choice_counter
                          )
        span.end()
        return response

    return awrapper


def record_exception(span, start_time, exception, duration_histogram, chat_exception_counter):
    '''
    record openai chat exception to trace and metrics
    '''
    try:
        duration = time.time() - start_time if "start_time" in locals() else 0
        if span.is_recording:
            span.record_exception(exception=exception)
        if MetricContext.metric_initialized():
            labels = {
                "error.type": exception.__class__.__name__,
            }
            if duration_histogram:
                MetricContext.histogram_record(
                    duration_histogram, duration, labels=labels)
            if chat_exception_counter:
                MetricContext.count(
                    chat_exception_counter, 1, labels=labels)
    except Exception as e:
        logger.warning(f"openai instrument record exception error.{e}")


def record_completion(span,
                      start_time,
                      response,
                      request_kwargs,
                      instance,
                      tokens_usage_histogram,
                      duration_histogram,
                      chat_choice_counter):
    '''
    Record chat completion to trace and metrics
    '''
    duration = time.time() - start_time if "start_time" in locals() else 0
    response_dict = model_as_dict(response)
    attributes = parse_openai_response(
        response_dict, request_kwargs, instance, False)
    usage = response_dict.get("usage")
    choices = response_dict.get("choices")
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")

    span_attributes = {
        **attributes,
        "llm.prompt_tokens": prompt_tokens,
        "llm.completion_tokens": completion_tokens,
        "llm.duration": duration
    }
    span_attributes.update(parse_response_message(choices))
    span.set_attributes(span_attributes)
    record_chat_response_metric(attributes=attributes,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                tokens_usage_histogram=tokens_usage_histogram,
                                duration=duration,
                                duration_histogram=duration_histogram,
                                choices=choices,
                                chat_choice_counter=chat_choice_counter
                                )


class WrappedStreamResponse(wrapt.ObjectProxy):

    def __init__(
        self,
        span,
        response,
        instance=None,
        start_time=None,
        tokens_usage_histogram: MetricTemplate = None,
        chat_choice_counter: MetricTemplate = None,
        duration_histogram: MetricTemplate = None,
        streaming_time_to_first_token_histogram: MetricTemplate = None,
        streaming_time_to_generate_histogram: MetricTemplate = None,
        request_kwargs=None
    ):
        super().__init__(response)
        self._span = span
        self._instance = instance
        self._start_time = start_time
        self._complete_response = {"choices": [], "model": ""}
        self._first_token_recorded = False
        self._tokens_usage_histogram = tokens_usage_histogram
        self._chat_choice_counter = chat_choice_counter
        self._duration_histogram = duration_histogram
        self._streaming_time_to_first_token_histogram = streaming_time_to_first_token_histogram
        self._streaming_time_to_generate_histogram = streaming_time_to_generate_histogram
        self._request_kwargs = request_kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__wrapped__.__exit__(exc_type, exc_val, exc_tb)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        return self

    def __aiter__(self):
        return self

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
        except Exception as e:
            if isinstance(e, StopIteration):
                self._close_span()
            raise e
        else:
            self._process_stream_chunk(chunk)
            return chunk

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
        except Exception as e:
            if isinstance(e, StopAsyncIteration):
                self._close_span()
            raise e
        else:
            self._process_stream_chunk(chunk)
            return chunk

    def _process_stream_chunk(self, chunk):
        record_stream_response_chunk(chunk, self._complete_response)
        if not self._first_token_recorded and self._streaming_time_to_first_token_histogram:
            self._time_of_first_token = time.time()
            duration = self._time_of_first_token - self._start_time
            attribute = parse_openai_response(
                self._complete_response, self._request_kwargs, self._instance, True)
            if MetricContext.metric_initialized():
                MetricContext.histogram_record(
                    self._streaming_time_to_first_token_histogram, duration, labels=attribute)
            self._first_token_recorded = True

    def _close_span(self):
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
        attributes = parse_openai_response(
            self._complete_response, self._request_kwargs, self._instance, True)
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
                                    tokens_usage_histogram=self._tokens_usage_histogram,
                                    duration=duration,
                                    duration_histogram=self._duration_histogram,
                                    choices=choices,
                                    chat_choice_counter=self._chat_choice_counter
                                    )
        if MetricContext.metric_initialized():
            if self._streaming_time_to_generate_histogram and first_token_to_generate_duration:
                MetricContext.histogram_record(
                    self._streaming_time_to_generate_histogram, first_token_to_generate_duration, labels=attribute)

        self._span.end()


def record_chat_response_metric(attributes,
                                prompt_tokens,
                                completion_tokens,
                                tokens_usage_histogram,
                                duration,
                                duration_histogram,
                                choices,
                                chat_choice_counter
                                ):
    if MetricContext.metric_initialized():
        if prompt_tokens and tokens_usage_histogram:
            labels = {
                **attributes,
                "llm.prompt_usage_type": "prompt_tokens"
            }
            MetricContext.histogram_record(
                tokens_usage_histogram, prompt_tokens, labels=labels)
        if completion_tokens and tokens_usage_histogram:
            labels = {
                **attributes,
                "llm.prompt_usage_type": "completion_tokens"
            }
            MetricContext.histogram_record(
                tokens_usage_histogram, completion_tokens, labels=labels)
        if duration and duration_histogram:
            MetricContext.histogram_record(
                duration_histogram, duration, labels=attributes)
        if choices and chat_choice_counter:
            MetricContext.count(chat_choice_counter,
                                len(choices), labels=attributes)
            for choice in choices:
                if choice.get("finish_reason"):
                    finish_reason_attr = {
                        **attributes,
                        "llm.finish_reason": choice.get("finish_reason")
                    }
                    MetricContext.count(
                        chat_choice_counter, 1, labels=finish_reason_attr)


class OpenAIInstrumentor(Instrumentor):

    tokens_usage_histogram = MetricTemplate(
        type=MetricType.HISTOGRAM,
        name="llm_token_usage",
        unit="token",
        description="Measures number of input and output tokens used"
    )

    chat_choice_counter = MetricTemplate(
        type=MetricType.COUNTER,
        name="llm_generation_choice_counter",
        unit="choice",
        description="Number of choices returned by chat completions call"
    )

    duration_histogram = MetricTemplate(
        type=MetricType.HISTOGRAM,
        name="llm_chat_duration",
        unit="s",
        description="AI chat duration",
    )

    chat_exception_counter = MetricTemplate(
        type=MetricType.COUNTER,
        name="llm_chat_exception_counter",
        unit="time",
        description="Number of exceptions occurred during chat completions",
    )

    streaming_time_to_first_token_histogram = MetricTemplate(
        type=MetricType.HISTOGRAM,
        name="llm_streaming_time_to_first_token",
        unit="s",
        description="Time to first token in streaming chat completions",
    )
    streaming_time_to_generate_histogram = MetricTemplate(
        type=MetricType.HISTOGRAM,
        name="streaming_time_to_generate",
        unit="s",
        description="Time between first token and completion in streaming chat completions",
    )

    def instrumentation_dependencies(self) -> Collection[str]:
        return ("openai >= 1.0.0",)

    def _instrument(self, **kwargs):
        tracer_provider = kwargs.get("tracer_provider")
        tracer = tracer_provider.get_tracer(
            "aworld.trace.instrumentation.openai")

        wrapt.wrap_function_wrapper(
            "openai.resources.chat.completions",
            "Completions.create",
            _chat_wrapper(tracer=tracer,
                          tokens_usage_histogram=self.tokens_usage_histogram,
                          chat_choice_counter=self.chat_choice_counter,
                          duration_histogram=self.duration_histogram,
                          chat_exception_counter=self.chat_exception_counter,
                          streaming_time_to_first_token_histogram=self.streaming_time_to_first_token_histogram,
                          streaming_time_to_generate_histogram=self.streaming_time_to_generate_histogram
                          )
        )

        wrapt.wrap_function_wrapper(
            "openai.resources.chat.completions",
            "AsyncCompletions.create",
            _achat_wrapper(
                tracer,
                tokens_usage_histogram=self.tokens_usage_histogram,
                chat_choice_counter=self.chat_choice_counter,
                duration_histogram=self.duration_histogram,
                chat_exception_counter=self.chat_exception_counter,
                streaming_time_to_first_token_histogram=self.streaming_time_to_first_token_histogram,
                streaming_time_to_generate_histogram=self.streaming_time_to_generate_histogram
            ),
        )

    def _instrument(self, **kwargs: Any):
        pass


@wrapt.decorator
async def performance_logger(wrapped, instance, args, kwargs):
    logger.info(f"before execute")
    result = await wrapped(*args, **kwargs)
    logger.info(f"after execute")
    return result


def wrap_openai(client: Union[openai.OpenAI, openai.AsyncOpenAI]):
    """Patch the OpenAI client to make it traceable.
       Example:
       client = wrap_openai(openai.OpenAI())
    """
    try:
        tracer_provider = get_tracer_provider_silent()
        if not tracer_provider:
            return
        tracer = tracer_provider.get_tracer(
            "aworld.trace.instrumentation.openai")

        if isinstance(client, openai.OpenAI):
            wrapper = _chat_wrapper(
                tracer,
                OpenAIInstrumentor.tokens_usage_histogram,
                OpenAIInstrumentor.chat_choice_counter,
                OpenAIInstrumentor.duration_histogram,
                OpenAIInstrumentor.chat_exception_counter,
                OpenAIInstrumentor.streaming_time_to_first_token_histogram,
                OpenAIInstrumentor.streaming_time_to_generate_histogram
            )
            client.chat.completions.create = wrapper(
                client.chat.completions.create)
            logger.info(
                f"[{client.__class__}]client.chat.completions.create be warpped")
        if isinstance(client, openai.AsyncOpenAI):
            awrapper = _achat_wrapper(
                tracer,
                OpenAIInstrumentor.tokens_usage_histogram,
                OpenAIInstrumentor.chat_choice_counter,
                OpenAIInstrumentor.duration_histogram,
                OpenAIInstrumentor.chat_exception_counter,
                OpenAIInstrumentor.streaming_time_to_first_token_histogram,
                OpenAIInstrumentor.streaming_time_to_generate_histogram
            )
            client.chat.completions.create = awrapper(
                client.chat.completions.create)
            logger.info(
                f"[{client.__class__}]client.chat.completions.create be warpped")
    except Exception:
        logger.warning(traceback.format_exc())

    return client
