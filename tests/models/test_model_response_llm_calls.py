from openai.types.chat.chat_completion import ChatCompletion

from aworld.models.model_response import ModelResponse
from aworld.models.openai_provider import OpenAIProvider


def test_from_openai_response_preserves_normalized_and_raw_usage_for_dict_response():
    response = {
        "id": "chatcmpl_123",
        "model": "gpt-4.1",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "hi"},
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "prompt_tokens_details": {"cached_tokens": 80},
            "cache_hit_tokens": 80,
        },
        "request_id": "req_provider_123",
    }

    model_response = ModelResponse.from_openai_response(response)

    assert model_response.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 125,
    }
    assert model_response.raw_usage == {
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 125,
        "prompt_tokens_details": {"cached_tokens": 80},
        "cache_hit_tokens": 80,
    }
    assert model_response.provider_request_id == "req_provider_123"


def test_from_openai_response_preserves_openai_object_request_id_and_raw_usage():
    response = ChatCompletion.model_validate(
        {
            "id": "chatcmpl_456",
            "object": "chat.completion",
            "created": 123,
            "model": "gpt-4.1",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "hello"},
                }
            ],
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 50,
                "total_tokens": 250,
                "prompt_tokens_details": {"cached_tokens": 150},
            },
        }
    )
    object.__setattr__(response, "_request_id", "req_provider_456")

    model_response = ModelResponse.from_openai_response(response)

    assert model_response.usage == {
        "prompt_tokens": 200,
        "completion_tokens": 50,
        "total_tokens": 250,
    }
    assert model_response.raw_usage["prompt_tokens"] == 200
    assert model_response.raw_usage["prompt_tokens_details"]["cached_tokens"] == 150
    assert model_response.provider_request_id == "req_provider_456"


def test_from_openai_stream_chunk_preserves_raw_usage_and_provider_request_id():
    chunk = {
        "id": "chatcmpl_stream_direct",
        "model": "gpt-4.1",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "delta": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": "{\"query\":\"llm calls\"}",
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "prompt_tokens_details": {"cached_tokens": 80},
            "cache_hit_tokens": 80,
        },
        "request_id": "req_provider_stream_direct",
    }

    model_response = ModelResponse.from_openai_stream_chunk(chunk)

    assert model_response.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 125,
    }
    assert model_response.raw_usage["cache_hit_tokens"] == 80
    assert model_response.provider_request_id == "req_provider_stream_direct"


def test_postprocess_stream_response_preserves_raw_usage_and_request_id_for_buffered_tool_call_finish():
    provider = OpenAIProvider(model_name="gpt-4.1", sync_enabled=False, async_enabled=False)

    partial_chunk = {
        "id": "chatcmpl_stream_buffered",
        "model": "gpt-4.1",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "finish_reason": None,
                "delta": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_456",
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": "{\"query\":",
                            },
                        }
                    ],
                },
            }
        ],
    }
    finish_chunk = {
        "id": "chatcmpl_stream_buffered",
        "model": "gpt-4.1",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "delta": {
                    "role": "assistant",
                    "content": None,
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "prompt_tokens_details": {"cached_tokens": 80},
            "cache_hit_tokens": 80,
        },
        "request_id": "req_provider_stream_buffered",
    }

    partial_response, partial_finish_reason = provider.postprocess_stream_response(partial_chunk)
    buffered_response, finish_reason = provider.postprocess_stream_response(finish_chunk)

    assert partial_response is None
    assert partial_finish_reason is None
    assert finish_reason == "tool_calls"
    assert buffered_response.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 125,
    }
    assert buffered_response.raw_usage["cache_hit_tokens"] == 80
    assert buffered_response.provider_request_id == "req_provider_stream_buffered"
