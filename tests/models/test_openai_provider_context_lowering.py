from __future__ import annotations

import json
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from aworld.config import ModelConfig
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    CandidateCompilePolicy,
    CandidateRequestNotEnforceable,
    canonical_json_hash,
)
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse
from aworld.models.openai_provider import OpenAIProvider


class _SyncCompletions:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return [object()]
        return object()


class _AsyncCompletions:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not kwargs.get("stream"):
            return object()

        async def chunks():
            yield object()

        return chunks()


def _response(kind: str) -> ModelResponse:
    return ModelResponse(
        id=f"response-{kind}",
        model="gpt-test",
        content=kind,
        message={"role": "assistant", "content": kind},
        finish_reason="stop",
    )


def _provider() -> tuple[OpenAIProvider, list[dict], list[dict]]:
    sync_calls: list[dict[str, Any]] = []
    async_calls: list[dict[str, Any]] = []
    provider = object.__new__(OpenAIProvider)
    provider.model_name = "gpt-test"
    provider.kwargs = {}
    provider.provider = SimpleNamespace(
        chat=SimpleNamespace(completions=_SyncCompletions(sync_calls))
    )
    provider.async_provider = SimpleNamespace(
        chat=SimpleNamespace(completions=_AsyncCompletions(async_calls))
    )
    provider.is_http_provider = False
    provider.stream_tool_buffer = []
    provider.postprocess_response = MethodType(
        lambda self, response: _response("completion"), provider
    )
    provider.postprocess_stream_response = MethodType(
        lambda self, chunk: (_response("stream"), "stop"), provider
    )
    return provider, sync_calls, async_calls


def _policy(*, enforce_ready: bool = True) -> CandidateCompilePolicy:
    return CandidateCompilePolicy(
        compiler_version="openai-lowering-test-v1",
        candidate_payload={
            "messages": [{"role": "user", "content": "candidate-secret"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "candidate_tool", "parameters": {}},
                }
            ],
            "params": {
                "temperature": 0.25,
                "max_tokens": 17,
                "stop": ["candidate-stop"],
            },
        },
        enforce_ready=enforce_ready,
    )


def _model(provider: OpenAIProvider, *, policy: CandidateCompilePolicy | None = None):
    model = LLMModel(
        conf=ModelConfig(context_compiler={
            "mode": "enforce",
            "compiler_version": "openai-lowering-test-v1",
        }),
        custom_provider=provider,
        context_candidate_policy=policy or _policy(),
    )
    # A custom provider is used only to avoid real credentials in this test;
    # the production construction path assigns this provider name itself.
    model.provider_name = "openai"
    return model


def _assert_lowering_receipt(context: Context, sent: dict[str, Any]) -> None:
    record = context.get_llm_calls()[0]
    rollout = record["context_rollout"]
    receipt = rollout["provider_lowering"]

    assert record["status"] == "success"
    assert record["capture_stage"] == "model_boundary"
    assert record["capture_fidelity"] == "model_boundary"
    assert record["request"]["messages"] == [
        {"role": "user", "content": "candidate-secret"}
    ]
    assert record["request_selection"] == "candidate"
    assert record["context_observe_scope"] == "legacy_request_before_rollout"
    assert record["provider_invoked"] is True
    assert record["provider_prepared_request_match"] is None
    assert rollout["candidate_status"] == "provider_lowered"
    assert rollout["candidate_applied"] is True
    assert rollout["provider_lowering_ready"] is True
    assert receipt["candidate_content_hash"] == rollout["candidate_snapshot"][
        "content_hash"
    ]
    assert receipt["provider_request"]["content_hash"] == canonical_json_hash(sent)
    assert receipt["provider_request"]["capture_stage"] == "provider_prepared"
    assert receipt["provider_request"]["fidelity"] == "provider_prepared"
    assert receipt["adapter_identity"] == (
        "aworld.provider.openai.chat_completions"
    )
    assert "candidate-secret" not in repr(rollout)
    assert "provider_lowering" in json.dumps(record)


@pytest.mark.asyncio
async def test_openai_enforce_lowers_same_candidate_once_across_all_paths():
    provider, sync_calls, async_calls = _provider()
    model = _model(provider)
    legacy_messages = [{"role": "user", "content": "legacy-secret"}]
    legacy_tools = [
        {"type": "function", "function": {"name": "legacy_tool"}}
    ]
    contexts = [Context(task_id=f"openai-enforce-{index}") for index in range(4)]
    common = {
        "tools": legacy_tools,
        "response_format": {"type": "json_object"},
        "tool_choice": "auto",
        "reasoning_effort": "low",
    }

    await model.acompletion(legacy_messages, context=contexts[0], **common)
    model.completion(legacy_messages, context=contexts[1], **common)
    list(model.stream_completion(legacy_messages, context=contexts[2], **common))
    [chunk async for chunk in model.astream_completion(
        legacy_messages, context=contexts[3], **common
    )]

    assert len(sync_calls) == 2
    assert len(async_calls) == 2
    sent_requests = [async_calls[0], sync_calls[0], sync_calls[1], async_calls[1]]
    for index, sent in enumerate(sent_requests):
        assert sent["messages"] == [
            {"role": "user", "content": "candidate-secret"}
        ]
        assert sent["tools"][0]["function"]["name"] == "candidate_tool"
        assert sent["temperature"] == 0.25
        assert sent["max_tokens"] == 17
        assert sent["stop"] == ["candidate-stop"]
        assert sent["response_format"] == {"type": "json_object"}
        assert sent["tool_choice"] == "auto"
        assert sent["reasoning_effort"] == "low"
        assert all(not key.startswith("_aworld_") for key in sent)
        assert "context" not in sent
        assert "llm_request_id" not in sent
        assert sent.get("stream", False) is (index >= 2)
        _assert_lowering_receipt(contexts[index], sent)


def test_openai_enforce_fails_closed_when_receipt_cannot_be_persisted():
    class BrokenCaptureContext(Context):
        def get_llm_calls(self):
            raise RuntimeError("private-storage-error")

    provider, sync_calls, _ = _provider()
    model = _model(provider)

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion(
            [{"role": "user", "content": "legacy"}],
            context=BrokenCaptureContext(task_id="broken-receipt"),
        )

    assert raised.value.reason_code == "provider_lowering_receipt_failed"
    assert sync_calls == []
    assert "private-storage-error" not in str(raised.value)


def test_openai_enforce_requires_explicit_compiler_readiness():
    provider, sync_calls, _ = _provider()
    model = _model(provider, policy=_policy(enforce_ready=False))
    context = Context(task_id="not-ready")

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion(
            [{"role": "user", "content": "legacy"}], context=context
        )

    assert raised.value.reason_code == "candidate_not_enforce_ready"
    assert sync_calls == []
    record = context.get_llm_calls()[0]
    assert record["status"] == "blocked_before_provider"
    assert record["provider_invoked"] is False


def test_openai_http_enforce_binds_the_same_provider_prepared_mapping():
    provider, _, _ = _provider()
    sent: list[dict[str, Any]] = []
    serialized: list[bytes] = []

    class HTTPHandler:
        def sync_call(self, data, *, serialized_body=None):
            sent.append(data)
            serialized.append(serialized_body)
            return object()

    provider.is_http_provider = True
    provider.http_provider = HTTPHandler()
    model = _model(provider)
    context = Context(task_id="openai-http-enforce")

    model.completion(
        [{"role": "user", "content": "legacy"}], context=context
    )

    assert len(sent) == 1
    assert serialized[0] is not None
    _assert_lowering_receipt(context, sent[0])
    assert context.get_llm_calls()[0]["capture_fidelity"] == "model_boundary"


def test_universal_final_http_enforce_records_serialized_cache_continuity():
    provider, _, _ = _provider()
    sent: list[tuple[dict[str, Any], bytes]] = []

    class HTTPHandler:
        def sync_call(self, data, *, serialized_body=None):
            sent.append((data, serialized_body))
            return object()

    provider.is_http_provider = True
    provider.http_provider = HTTPHandler()
    model = LLMModel(
        conf=ModelConfig(
            context_compiler={"mode": "enforce", "universal_final": True}
        ),
        custom_provider=provider,
    )
    model.provider_name = "openai"
    context = Context(task_id="universal-http-enforce")
    # ApplicationContext uses an empty trace id until tracing is configured.
    # Optional runtime identifiers must normalize before entering the sealed
    # final compiler contract.
    context.trace_id = ""
    system = {"role": "system", "content": "stable rules"}

    model.completion(
        [system, {"role": "user", "content": "first"}], context=context
    )
    model.completion(
        [system, {"role": "user", "content": "second"}], context=context
    )

    assert len(sent) == 2
    assert all(serialized_body for _, serialized_body in sent)
    first, second = context.get_llm_calls()
    assert first["context_rollout"]["final_compile"]["enforce"]["ready"]
    assert first["context_rollout"]["provider_lowering"][
        "cache_continuity"
    ]["status"] == "initialized"
    assert second["context_rollout"]["provider_lowering"][
        "cache_continuity"
    ]["status"] == "continued"


def test_openai_enforce_rejects_provider_transform_after_candidate():
    provider, sync_calls, _ = _provider()
    model = _model(provider)
    context = Context(task_id="post-candidate-transform")

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion(
            [{"role": "user", "content": "legacy"}],
            context=context,
            prompt_assembly_plan=object(),
        )

    assert raised.value.reason_code == "provider_transform_after_candidate"
    assert sync_calls == []
    assert context.get_llm_calls()[0]["provider_invoked"] is False


def test_openai_enforce_rejects_unsnapshotable_final_provider_params():
    provider, sync_calls, _ = _provider()
    model = _model(provider)
    context = Context(task_id="unsnapshotable")

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion(
            [{"role": "user", "content": "legacy"}],
            context=context,
            response_format=object(),
        )

    assert raised.value.reason_code == "provider_request_lowering_failed"
    assert sync_calls == []
    record = context.get_llm_calls()[0]
    assert record["status"] == "blocked_before_provider"
    assert record["provider_invoked"] is False
    assert record["context_rollout"]["error"] == {
        "code": "provider_request_lowering_failed"
    }
