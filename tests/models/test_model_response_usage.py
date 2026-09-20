import json

from aworld.models.model_response import ModelResponse
from aworld.models.usage import (
    CacheUsageFidelity,
    build_cache_usage_receipt,
    normalize_usage,
    reconcile_cache_usage_receipt,
    summarize_prompt_cache_usage,
)


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


def test_cache_usage_receipt_accepts_openai_compatible_explicit_zero():
    raw = {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "total_tokens": 105,
        "prompt_tokens_details": {"cached_tokens": 0},
    }
    normalized = {
        **raw,
        "cache_hit_tokens": 0,
    }

    receipt = build_cache_usage_receipt(
        raw_usage=raw,
        normalized_usage=normalized,
    )

    assert receipt.fidelity is CacheUsageFidelity.EXACT
    assert receipt.reason_code is None
    assert receipt.input_tokens == 100
    assert receipt.output_tokens == 5
    assert receipt.cache_read_tokens == 0
    assert receipt.cache_read_lower_bound == 0
    assert receipt.cache_read_upper_bound == 0
    assert receipt.uncached_input_tokens == 100
    assert receipt.reported_input_tokens == 100
    assert receipt.input_token_accounting == "inclusive"


def test_cache_usage_receipt_accepts_provider_neutral_anthropic_aliases():
    raw = {
        "input_tokens": 120,
        "output_tokens": 7,
        "cache_read_input_tokens": 80,
        "cache_creation_input_tokens": 20,
    }
    normalized = normalize_usage(raw)

    receipt = build_cache_usage_receipt(
        raw_usage=raw,
        normalized_usage=normalized,
    )

    assert receipt.fidelity is CacheUsageFidelity.EXACT
    assert receipt.input_tokens == 220
    assert receipt.reported_input_tokens == 120
    assert receipt.input_token_accounting == "exclusive_cache_components"
    assert receipt.cache_read_tokens == 80
    assert receipt.cache_write_tokens == 20
    assert receipt.cache_read_ratio == 80 / 220
    assert receipt.uncached_input_tokens == 140


def test_cache_usage_receipt_bounds_missing_cache_detail_instead_of_using_zero():
    raw = {
        "prompt_tokens": 90,
        "completion_tokens": 4,
        "total_tokens": 94,
    }
    normalized = dict(raw)

    receipt = build_cache_usage_receipt(
        raw_usage=raw,
        normalized_usage=normalized,
    )

    assert receipt.fidelity is CacheUsageFidelity.BOUNDED
    assert receipt.reason_code == "provider_cache_usage_missing"
    assert receipt.cache_read_tokens is None
    assert receipt.cache_read_lower_bound == 0
    assert receipt.cache_read_upper_bound == 90
    assert receipt.uncached_input_tokens is None


def test_cache_usage_receipt_rejects_conflicting_cache_aliases():
    raw = {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "cache_hit_tokens": 64,
        "prompt_tokens_details": {"cached_tokens": 32},
    }

    receipt = build_cache_usage_receipt(
        raw_usage=raw,
        normalized_usage={
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "cache_hit_tokens": 64,
        },
    )

    assert receipt.fidelity is CacheUsageFidelity.CONFLICTING
    assert receipt.reason_code == "provider_cache_usage_conflicting_aliases"
    assert receipt.cache_read_tokens is None


def test_cache_usage_receipt_rejects_cache_value_greater_than_input():
    receipt = build_cache_usage_receipt(
        raw_usage={
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 11},
        },
        normalized_usage={
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "cache_hit_tokens": 11,
        },
    )

    assert receipt.fidelity is CacheUsageFidelity.INVALID
    assert receipt.reason_code == "provider_cache_usage_exceeds_input"
    assert receipt.cache_read_tokens is None


def test_cache_usage_receipt_rejects_raw_and_normalized_cache_conflict():
    receipt = build_cache_usage_receipt(
        raw_usage={
            "prompt_tokens": 100,
            "completion_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 64},
        },
        normalized_usage={
            "prompt_tokens": 100,
            "completion_tokens": 1,
            "cache_hit_tokens": 32,
        },
    )

    assert receipt.fidelity is CacheUsageFidelity.CONFLICTING
    assert receipt.reason_code == "provider_cache_usage_conflicting_views"


def test_cache_usage_receipt_is_unavailable_without_provider_usage():
    receipt = build_cache_usage_receipt(raw_usage=None, normalized_usage=None)

    assert receipt.fidelity is CacheUsageFidelity.UNAVAILABLE
    assert receipt.reason_code == "provider_usage_unavailable"
    assert receipt.input_tokens is None
    assert receipt.cache_read_tokens is None


def test_normalize_usage_canonicalizes_componentized_input_accounting():
    normalized = normalize_usage(
        {
            "input_tokens": 12,
            "output_tokens": 3,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 5,
        }
    )

    assert normalized["prompt_tokens"] == 97
    assert normalized["input_tokens"] == 97
    assert normalized["completion_tokens"] == 3
    assert normalized["output_tokens"] == 3
    assert normalized["total_tokens"] == 100
    assert normalized["cache_hit_tokens"] == 80
    assert normalized["cache_write_tokens"] == 5


def test_reconcile_cache_usage_receipt_rejects_modified_capture():
    raw = {
        "prompt_tokens": 10,
        "completion_tokens": 1,
        "prompt_tokens_details": {"cached_tokens": 4},
    }
    captured = build_cache_usage_receipt(
        raw_usage=raw, normalized_usage=normalize_usage(raw)
    ).to_dict()
    captured["cache_read_tokens"] = 9

    reconciled = reconcile_cache_usage_receipt(
        captured_receipt=captured,
        raw_usage=raw,
        normalized_usage=normalize_usage(raw),
    )

    assert reconciled.fidelity is CacheUsageFidelity.CONFLICTING
    assert reconciled.reason_code == "captured_cache_usage_receipt_mismatch"
    assert reconciled.cache_read_tokens is None


def test_anthropic_response_normalizes_total_input_without_provider_core_branch():
    class Usage:
        def model_dump(self):
            return {
                "input_tokens": 12,
                "output_tokens": 3,
                "cache_read_input_tokens": 80,
                "cache_creation_input_tokens": 5,
            }

    class Response:
        id = "anthropic-response"
        model = "claude"
        content = []
        usage = Usage()

    response = ModelResponse.from_anthropic_response(Response())

    assert response.usage["prompt_tokens"] == 97
    assert response.usage["total_tokens"] == 100
    assert response.raw_usage["input_tokens"] == 12


def test_anthropic_stream_usage_event_is_preserved_for_receipt_selection():
    response = ModelResponse.from_anthropic_stream_chunk(
        {
            "id": "anthropic-stream",
            "model": "claude",
            "usage": {
                "input_tokens": 12,
                "output_tokens": 3,
                "cache_read_input_tokens": 80,
                "cache_creation_input_tokens": 5,
            },
        }
    )

    assert response.usage["prompt_tokens"] == 97
    assert response.usage["cache_hit_tokens"] == 80
    assert response.raw_usage["cache_creation_input_tokens"] == 5
