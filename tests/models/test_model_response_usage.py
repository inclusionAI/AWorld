import json

from aworld.models.model_response import ModelResponse
from aworld.models.usage import summarize_prompt_cache_usage


def test_from_openai_response_preserves_raw_usage_cache_details_and_request_id():
    response = {
        "id": "resp-1",
        "model": "gpt-4o-mini",
        "_request_id": "req-123",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "done",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
            "prompt_tokens_details": {"cached_tokens": 5},
        },
    }

    resp = ModelResponse.from_openai_response(response)

    assert resp.usage["prompt_tokens"] == 11
    assert resp.usage["completion_tokens"] == 7
    assert resp.usage["total_tokens"] == 18
    assert resp.usage["cache_hit_tokens"] == 5
    assert resp.raw_usage["prompt_tokens_details"]["cached_tokens"] == 5
    assert resp.provider_request_id == "req-123"
    assert resp.to_dict()["raw_usage"]["prompt_tokens_details"]["cached_tokens"] == 5


def test_from_openai_stream_chunk_preserves_usage_only_chunk_details():
    chunk = {
        "id": "chunk-1",
        "model": "gpt-4o-mini",
        "_request_id": "req-stream-1",
        "choices": [],
        "usage": {
            "prompt_tokens": 13,
            "completion_tokens": 3,
            "total_tokens": 16,
            "prompt_tokens_details": {"cached_tokens": 9},
        },
    }

    resp = ModelResponse.from_openai_stream_chunk(chunk)

    assert resp.content == ""
    assert resp.tool_calls is None
    assert resp.usage["prompt_tokens"] == 13
    assert resp.usage["completion_tokens"] == 3
    assert resp.usage["total_tokens"] == 16
    assert resp.usage["cache_hit_tokens"] == 9
    assert resp.raw_usage["prompt_tokens_details"]["cached_tokens"] == 9
    assert resp.provider_request_id == "req-stream-1"
    assert json.loads(repr(resp))["raw_usage"]["prompt_tokens_details"]["cached_tokens"] == 9


def test_summarize_prompt_cache_usage_derives_read_write_and_related_ratios():
    usage = {
        "prompt_tokens": 996553,
        "completion_tokens": 11536,
        "total_tokens": 1008089,
        "prompt_tokens_details": {
            "cached_tokens": 473760,
            "cache_creation_input_tokens": 355120,
            "cache_read_input_tokens": 118640,
        },
    }

    summary = summarize_prompt_cache_usage(usage)

    assert summary == {
        "cache_read_tokens": 118640,
        "cache_write_tokens": 355120,
        "cache_related_tokens": 473760,
        "cache_read_ratio": 0.1191,
        "cache_write_ratio": 0.3563,
        "cache_related_ratio": 0.4754,
    }
