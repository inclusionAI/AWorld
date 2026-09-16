import importlib.util
from pathlib import Path

import pytest

from aworld.models.model_response import ModelResponse


def _load_preflight():
    path = Path(__file__).resolve().parents[2] / "examples/sandbox/cache_usage_preflight.py"
    spec = importlib.util.spec_from_file_location("aworld_cache_usage_preflight_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _CacheModel:
    def __init__(self):
        self.calls = []

    @staticmethod
    def _response(cache_tokens: int, prompt_tokens: int = 4096):
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 1,
            "total_tokens": prompt_tokens + 1,
            "prompt_tokens_details": {"cached_tokens": cache_tokens},
        }
        return ModelResponse(
            id="response",
            model="provider-model",
            content="OK",
            usage=usage,
            raw_usage=usage,
        )

    async def acompletion(self, messages, **kwargs):
        self.calls.append(("nonstream", messages, kwargs))
        position = (len(self.calls) - 1) % 4
        return self._response((0, 3072, 3072, 0)[position])

    async def astream_completion(self, messages, **kwargs):
        self.calls.append(("stream", messages, kwargs))
        position = (len(self.calls) - 1) % 4
        yield ModelResponse(
            id="response",
            model="provider-model",
            content="OK",
        )
        yield self._response((0, 3072, 3072, 0)[position])


@pytest.mark.asyncio
async def test_provider_neutral_cache_preflight_covers_stream_and_nonstream():
    preflight = _load_preflight()
    model = _CacheModel()

    receipt = await preflight.probe_model_cache(
        model=model,
        provider="custom-provider",
        model_name="provider-model",
        timeout_sec=5,
        include_streaming=True,
        run_nonce="fixed-nonce",
        prefix_line_count=300,
    )

    assert receipt["schema_version"] == "aworld.cache-conformance-preflight/v1"
    assert receipt["status"] == "passed"
    assert receipt["provider"] == "custom-provider"
    assert receipt["model"] == "provider-model"
    assert receipt["cache_capability_observed"] is True
    assert receipt["exact_usage_coverage"] == 1.0
    assert len(receipt["observations"]) == 8
    assert {item["mode"] for item in receipt["observations"]} == {
        "nonstream",
        "stream",
    }
    assert all("messages" not in item for item in receipt["observations"])
    assert all("response" not in item for item in receipt["observations"])
    assert all(item["request_fingerprint"].startswith("sha256:") for item in receipt["observations"])
    assert len(model.calls) == 8


def test_cache_preflight_rejects_missing_usage_without_manufacturing_zero():
    preflight = _load_preflight()
    observations = [
        {
            "label": label,
            "mode": "nonstream",
            "status": "success",
            "cache_usage_receipt": {
                "fidelity": "bounded",
                "cache_read_tokens": None,
                "reason_code": "provider_cache_usage_missing",
            },
        }
        for label in ("cold", "repeat", "suffix_change", "prefix_change")
    ]

    decision = preflight.evaluate_cache_conformance(observations)

    assert decision["status"] == "failed"
    assert decision["exact_usage_coverage"] == 0.0
    assert decision["cache_capability_observed"] is False
    assert "cache_usage_not_exact" in decision["failure_codes"]


def test_cache_preflight_requires_behavioral_hit_and_invalidation():
    preflight = _load_preflight()
    cache_values = {
        "cold": 0,
        "repeat": 0,
        "suffix_change": 0,
        "prefix_change": 0,
    }
    observations = [
        {
            "label": label,
            "mode": "nonstream",
            "status": "success",
            "cache_usage_receipt": {
                "fidelity": "exact",
                "cache_read_tokens": value,
                "reason_code": None,
            },
        }
        for label, value in cache_values.items()
    ]

    decision = preflight.evaluate_cache_conformance(observations)

    assert decision["status"] == "failed"
    assert decision["exact_usage_coverage"] == 1.0
    assert decision["cache_capability_observed"] is False
    assert "repeat_cache_hit_not_observed" in decision["failure_codes"]
    assert "suffix_prefix_reuse_not_observed" in decision["failure_codes"]


def test_cache_preflight_can_validate_nonstream_only_provider():
    preflight = _load_preflight()
    observations = []
    for label, value in {
        "cold": 0,
        "repeat": 3000,
        "suffix_change": 3000,
        "prefix_change": 0,
    }.items():
        observations.append(
            {
                "label": label,
                "mode": "nonstream",
                "status": "success",
                "cache_usage_receipt": {
                    "fidelity": "exact",
                    "cache_read_tokens": value,
                    "reason_code": None,
                },
            }
        )

    decision = preflight.evaluate_cache_conformance(observations)

    assert decision["status"] == "passed"
    assert decision["validated_modes"] == ["nonstream"]
