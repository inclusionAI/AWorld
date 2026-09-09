from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_preflight():
    path = Path(__file__).resolve().parents[2] / "examples/sandbox/model_preflight.py"
    spec = spec_from_file_location("aworld_model_preflight_test", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeModel:
    response = None
    last_kwargs = None

    def __init__(self, conf):
        self.conf = conf

    async def acompletion(self, **kwargs):
        type(self).last_kwargs = kwargs
        return self.response


@pytest.mark.asyncio
async def test_reasoning_only_truncated_response_proves_provider_connectivity(
    monkeypatch,
):
    preflight = _load_preflight()
    monkeypatch.setenv("LLM_MODEL_NAME", "reasoning-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr("aworld.models.llm.LLMModel", _FakeModel)
    _FakeModel.response = SimpleNamespace(
        id="response-1",
        content="",
        reasoning_content="provider returned reasoning",
        error=None,
        finish_reason="length",
        usage={"prompt_tokens": 20, "completion_tokens": 128},
    )

    receipt = await preflight.probe(1, 7)

    assert receipt["status"] == "passed"
    assert receipt["provider_response_observed"] is True
    assert receipt["semantic_probe_complete"] is False
    assert receipt["tool_call_probe_complete"] is False
    assert receipt["response_quality"] == "degraded"
    assert receipt["quality_reason_code"] == "response_truncated_after_reasoning"
    assert _FakeModel.last_kwargs["max_tokens"] == 512
    assert _FakeModel.last_kwargs["tools"][0]["function"]["name"] == "health_probe"


@pytest.mark.asyncio
async def test_tool_call_probe_matches_real_benchmark_request_shape(monkeypatch):
    preflight = _load_preflight()
    monkeypatch.setenv("LLM_MODEL_NAME", "tool-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr("aworld.models.llm.LLMModel", _FakeModel)
    _FakeModel.response = SimpleNamespace(
        id="response-tool",
        content="",
        reasoning_content="",
        error=None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "function": {
                    "name": "health_probe",
                    "arguments": '{"value":"READY"}',
                }
            }
        ],
        usage={"prompt_tokens": 30, "completion_tokens": 10},
    )

    receipt = await preflight.probe(1, 7)

    assert receipt["status"] == "passed"
    assert receipt["semantic_probe_complete"] is True
    assert receipt["tool_call_probe_complete"] is True
    assert receipt["response_quality"] == "complete"


@pytest.mark.asyncio
async def test_provider_error_still_fails_connectivity_preflight(monkeypatch):
    preflight = _load_preflight()
    monkeypatch.setenv("LLM_MODEL_NAME", "reasoning-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr("aworld.models.llm.LLMModel", _FakeModel)
    _FakeModel.response = SimpleNamespace(
        id="response-1",
        content="",
        reasoning_content="",
        error="unavailable",
        finish_reason="",
        usage={},
    )

    with pytest.raises(RuntimeError, match="error response"):
        await preflight.probe(1, 7)
