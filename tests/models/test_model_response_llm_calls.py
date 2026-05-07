from openai.types.chat.chat_completion import ChatCompletion

from aworld.models.model_response import ModelResponse


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
