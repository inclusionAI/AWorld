import copy
import json
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse
from aworld.trace.base import Span
from aworld.trace.instrumentation.openai.inout_parse import should_trace_prompts
from aworld.logs.util import logger


def parser_request_params(kwargs, instance: LLMModel):
    attributes = {
        "llm.system": instance.provider_name,
        "llm.model": instance.provider.model_name,
        "llm.max_tokens": kwargs.get("max_tokens", ""),
        "llm.temperature": kwargs.get("temperature", ""),
        "llm.stop": str(kwargs.get("stop", "")),
        "llm.frequency_penalty": kwargs.get("frequency_penalty", ""),
        "llm.presence_penalty": kwargs.get("presence_penalty", ""),
        "llm.user": kwargs.get("user", ""),
        "llm.stream": kwargs.get("stream", "")
    }
    return attributes


async def handle_request(span: Span, kwargs, instance):
    if not span or not span.is_recording():
        return
    try:
        attributes = parser_request_params(kwargs, instance)
        if should_trace_prompts():
            messages = kwargs.get("messages")
            for i, msg in enumerate(messages):
                prefix = f"llm.prompts.{i}"
                attributes.update({f"{prefix}.role": msg.get("role")})
                if msg.get("content"):
                    content = copy.deepcopy(msg.get("content"))
                    content = json.dumps(content, ensure_ascii=False)
                    attributes.update({f"{prefix}.content": content})
                if msg.get("tool_call_id"):
                    attributes.update({
                        f"{prefix}.tool_call_id": msg.get("tool_call_id")})
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for i, tool_call in enumerate(tool_calls):
                        function = tool_call.get("function")
                        attributes.update({
                            f"{prefix}.tool_calls.{i}.id": tool_call.get("id")})
                        attributes.update({
                            f"{prefix}.tool_calls.{i}.name": function.get("name")})
                        attributes.update({
                            f"{prefix}.tool_calls.{i}.arguments": function.get("arguments")})

        filterd_attri = {k: v for k, v in attributes.items()
                         if (v and v is not "")}

        span.set_attributes(filterd_attri)
    except ValueError as e:
        logger.warning(f"trace handle openai request error: {e}")


def get_common_attributes_from_response(instance: LLMModel, is_async, is_streaming):
    operation = "acompletion" if is_async else "completion"
    if is_streaming:
        operation = "astream_completion" if is_async else "stream_completion"
    return {
        "llm.system": instance.provider_name,
        "llm.response.model": instance.provider.model_name,
        "llm.operation.name": operation,
        "llm.server.address": instance.provider.base_url,
        "llm.stream": is_streaming,
    }


def accumulate_stream_response(chunk: ModelResponse, complete_response: dict):
    logger.info(f"accumulate_stream_response chunk= {chunk}")
    pass


def record_stream_token_usage(complete_response, request_kwargs) -> tuple[int, int]:
    '''
        return (prompt_usage, completion_usage)
    '''
    logger.info(
        f"record_stream_token_usage complete_response= {complete_response}")
    return (0, 0)


def parse_response_message(tool_calls) -> dict:
    attributes = {}
    prefix = "llm.completions"
    if tool_calls:
        for i, tool_call in enumerate(tool_calls):
            function = tool_call.get("function")
            attributes.update(
                {f"{prefix}.tool_calls.{i}.id": tool_call.get("id")})
            attributes.update(
                {f"{prefix}.tool_calls.{i}.name": function.get("name")})
            attributes.update(
                {f"{prefix}.tool_calls.{i}.arguments": function.get("arguments")})
    return attributes


def response_to_dic(response: ModelResponse) -> dict:
    logger.info(f"completion response= {response}")
    return response.to_dict()
