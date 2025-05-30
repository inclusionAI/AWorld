import re
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse
from aworld.logs.util import logger


def get_common_attributes_from_response(instance: LLMModel, is_async, is_streaming):
    operation = "acompletion" if is_async else "completion"
    if is_streaming:
        operation = "astream_completion" if is_async else "stream_completion"
    return {
        "llm.systerm": instance.provider_name,
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


def parse_response_message(choices) -> dict:
    return {}


def response_to_dic(response) -> dict:
    logger.info(f"completion response= {response}")
    return {}
