from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from aworld.config import ModelConfig
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    CanaryHealthEvidence,
    CanaryHealthPolicy,
    CandidateCompilePolicy,
    CandidateRequestNotEnforceable,
    ContextLifecycleState,
    ContextEntrypointParityReceipt,
    ReadinessStatus,
    RollbackBundle,
    RolloutCapability,
    VerifiedContextEntrypointParityReceipt,
    assess_canary_health,
    assess_default_on_readiness,
    assess_entrypoint_parity,
    canonical_json_hash,
)
from aworld.core.trajectory import (
    TrajectoryBuildResult,
    TrajectoryBuildStatus,
    TrajectoryFidelity,
    TrajectorySourceKind,
)
from aworld.models.llm import LLMModel
from aworld.models.model_response import LLMResponseError, ModelResponse
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


def _observe_model(provider: OpenAIProvider):
    model = LLMModel(
        conf=ModelConfig(context_compiler={"mode": "observe"}),
        custom_provider=provider,
    )
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
    assert record["provider_request"]["payload"] == sent
    assert record["provider_request"]["capture_stage"] == "provider_prepared"
    assert record["provider_request"]["fidelity"] == "provider_prepared"
    assert rollout["candidate_status"] == "provider_attempted"
    assert record["provider_attempt_status"] == "attempted"
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
    attribution = receipt["attribution"]
    compiler_plan = rollout["compiler_attribution_plan"]
    assert attribution["status"] == "available"
    assert attribution["plan_fingerprint"] == compiler_plan["plan_fingerprint"]
    assert rollout["candidate_snapshot"]["attribution_plan_fingerprint"] == compiler_plan["plan_fingerprint"]
    assert attribution["byte_conservation"] is True
    assert (
        attribution["attributed_value_bytes"]
        + attribution["provider_envelope_and_params"]
        == attribution["total_canonical_bytes"]
    )
    assert "candidate-secret" not in repr(rollout)
    assert "provider_lowering" in json.dumps(record)


def test_openai_off_mode_captures_provider_prepared_request_before_send():
    provider, sync_calls, _ = _provider()
    model = LLMModel(custom_provider=provider)
    model.provider_name = "openai"
    context = Context(task_id="openai-off-provider-capture")

    model.completion(
        [{"role": "user", "content": "legacy"}],
        context=context,
        response_format={"type": "json_object"},
    )

    assert len(sync_calls) == 1
    record = context.get_llm_calls()[0]
    assert record["provider_request"]["payload"] == sync_calls[0]
    assert record["provider_request"]["capture_stage"] == "provider_prepared"
    assert record["provider_request"]["fidelity"] == "provider_prepared"
    assert record["provider_request"]["content_hash"] == canonical_json_hash(
        sync_calls[0]
    )
    assert record["provider_invoked"] is True
    assert "context_rollout" not in record


def test_openai_observe_is_byte_compatible_and_does_not_compile_or_leak_metadata(monkeypatch):
    off_provider, off_calls, _ = _provider()
    observe_provider, observe_calls, _ = _provider()
    off = LLMModel(custom_provider=off_provider)
    off.provider_name = "openai"
    observe = _observe_model(observe_provider)
    monkeypatch.setattr(
        "aworld.models.llm.compile_context_candidate",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("candidate compiled")),
    )
    messages = [{"role": "user", "content": "same"}]
    tools = [{"type": "function", "function": {"name": "same_tool"}}]
    kwargs = {"tools": tools, "response_format": {"type": "json_object"}}

    off.completion(messages, context=Context(task_id="off-bytes"), **kwargs)
    context = Context(task_id="observe-bytes")
    observe.completion(messages, context=context, **kwargs)

    assert off_calls == observe_calls
    assert len(observe_calls) == 1
    assert all(not key.startswith("_aworld_") for key in observe_calls[0])
    assert "context" not in observe_calls[0]
    rollout = context.get_llm_calls()[0]["context_rollout"]
    assert rollout["candidate_status"] == "not_requested"
    assert rollout["provider_attribution"]["subject"] == "legacy_observed"
    assert rollout["provider_attribution"]["status"] == "available"
    assert rollout["provider_attribution"]["attribution"]["byte_conservation"] is True


def test_openai_observe_capture_failure_is_fail_open_and_sends_once():
    class BrokenCaptureContext(Context):
        def get_llm_calls(self):
            raise RuntimeError("storage-failed")

    provider, sync_calls, _ = _provider()
    model = _observe_model(provider)

    model.completion(
        [{"role": "user", "content": "legacy"}],
        context=BrokenCaptureContext(task_id="observe-fail-open"),
    )

    assert len(sync_calls) == 1


def test_openai_observe_attribution_commit_failure_degrades_to_attempt_truth():
    class FailFirstReplacement(list):
        def __init__(self):
            super().__init__()
            self.failed = False

        def __setitem__(self, index, value):
            if not self.failed:
                self.failed = True
                raise RuntimeError("observe-commit-failed")
            return super().__setitem__(index, value)

    class RecoverableContext(Context):
        def __init__(self):
            super().__init__(task_id="observe-commit-fail-open")
            self.calls = FailFirstReplacement()

        def get_llm_calls(self):
            return self.calls

    provider, sync_calls, _ = _provider()
    context = RecoverableContext()

    _observe_model(provider).completion(
        [{"role": "user", "content": "legacy"}], context=context
    )

    assert len(sync_calls) == 1
    record = context.calls[0]
    assert record["provider_invoked"] is True
    assert record["provider_attempt_status"] == "attempted"
    assert record["provider_request"]["payload"] == sync_calls[0]
    assert record["context_rollout"]["provider_attribution"] == {
        **record["context_rollout"]["provider_attribution"],
        "status": "unavailable",
        "reason_code": "provider_attribution_storage_failed",
    }


@pytest.mark.asyncio
async def test_openai_observe_opaque_sdk_params_match_off_once_sync_and_async():
    opaque = object()
    off_provider, off_sync, off_async = _provider()
    observed_provider, observed_sync, observed_async = _provider()
    off = LLMModel(custom_provider=off_provider)
    off.provider_name = "openai"
    observed = _observe_model(observed_provider)
    messages = [{"role": "user", "content": "same"}]

    off.completion(messages, context=Context(task_id="opaque-off-sync"), response_format=opaque)
    observed_sync_context = Context(task_id="opaque-observe-sync")
    observed.completion(messages, context=observed_sync_context, response_format=opaque)
    await off.acompletion(messages, context=Context(task_id="opaque-off-async"), response_format=opaque)
    observed_async_context = Context(task_id="opaque-observe-async")
    await observed.acompletion(messages, context=observed_async_context, response_format=opaque)

    assert off_sync == observed_sync
    assert off_async == observed_async
    assert len(observed_sync) == len(observed_async) == 1
    for sent in (*observed_sync, *observed_async):
        assert sent["response_format"] is opaque
        assert all(not key.startswith("_aworld_") for key in sent)
        assert "context" not in sent
    for context in (observed_sync_context, observed_async_context):
        record = context.get_llm_calls()[0]
        assert record["provider_invoked"] is True
        assert record["provider_attempt_status"] == "attempted"
        assert record["context_rollout"]["provider_attribution"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_openai_observe_stream_close_preserves_attempt_truth_sync_and_async():
    provider, sync_calls, async_calls = _provider()
    model = _observe_model(provider)
    sync_context = Context(task_id="observe-sync-close")
    stream = model.stream_completion(
        [{"role": "user", "content": "stream"}], context=sync_context
    )
    next(stream)
    stream.close()

    async_context = Context(task_id="observe-async-close")
    astream = model.astream_completion(
        [{"role": "user", "content": "stream"}], context=async_context
    )
    await anext(astream)
    await astream.aclose()

    assert len(sync_calls) == len(async_calls) == 1
    for context in (sync_context, async_context):
        record = context.get_llm_calls()[0]
        assert record["provider_invoked"] is True
        assert record["provider_attempt_status"] == "attempted"
        assert record["status"] == "cancelled"


def test_openai_observe_provider_transform_is_unavailable_but_still_sends_once():
    provider, sync_calls, _ = _provider()
    provider.preprocess_messages = MethodType(
        lambda self, messages, **kwargs: [
            *messages, {"role": "system", "content": "provider-added"}
        ],
        provider,
    )
    model = _observe_model(provider)
    context = Context(task_id="observe-transform")

    model.completion([{"role": "user", "content": "legacy"}], context=context)

    assert len(sync_calls) == 1
    assert len(sync_calls[0]["messages"]) == 2
    evidence = context.get_llm_calls()[0]["context_rollout"]["provider_attribution"]
    assert evidence["status"] == "unavailable"
    assert evidence["reason_code"] == "provider_attribution_mismatch"


def test_openai_http_observe_binds_serialized_provider_payload():
    provider, _, _ = _provider()
    sent = []

    class HTTPHandler:
        def sync_call(self, data, *, serialized_body=None):
            sent.append((data, serialized_body))
            return object()

    provider.is_http_provider = True
    provider.http_provider = HTTPHandler()
    model = _observe_model(provider)
    context = Context(task_id="observe-http")

    model.completion([{"role": "user", "content": "legacy"}], context=context)

    assert len(sent) == 1
    assert sent[0][1] is not None
    record = context.get_llm_calls()[0]
    evidence = record["context_rollout"]["provider_attribution"]
    assert evidence["status"] == "available"
    assert evidence["attribution"]["serialization"] == "http_serialized_canonical_json"
    assert record["provider_request"]["serialized_checksum"] == canonical_json_hash(sent[0][0])


@pytest.mark.asyncio
async def test_openai_async_http_observe_binds_transport_owned_bytes_once():
    provider, _, _ = _provider()
    sent = []

    class HTTPHandler:
        async def async_call(self, data, *, serialized_body=None):
            sent.append((data, serialized_body))
            return object()

    provider.is_http_provider = True
    provider.http_provider = HTTPHandler()
    context = Context(task_id="observe-http-async")

    await _observe_model(provider).acompletion(
        [{"role": "user", "content": "legacy"}], context=context
    )

    assert len(sent) == 1
    assert sent[0][1] is not None
    evidence = context.get_llm_calls()[0]["context_rollout"]["provider_attribution"]
    assert evidence["status"] == "available"
    assert evidence["attribution"]["serialization"] == "http_serialized_canonical_json"


@pytest.mark.asyncio
async def test_openai_observe_provider_error_and_cancel_keep_attempt_truth():
    provider, sync_calls, async_calls = _provider()

    def sync_error(**kwargs):
        sync_calls.append(kwargs)
        raise RuntimeError("provider-error")

    async def async_cancel(**kwargs):
        async_calls.append(kwargs)
        raise asyncio.CancelledError()

    provider.provider.chat.completions.create = sync_error
    provider.async_provider.chat.completions.create = async_cancel
    model = _observe_model(provider)
    sync_context = Context(task_id="observe-provider-error")
    with pytest.raises(LLMResponseError):
        model.completion([{"role": "user", "content": "go"}], context=sync_context)
    async_context = Context(task_id="observe-provider-cancel")
    with pytest.raises(asyncio.CancelledError):
        await model.acompletion([{"role": "user", "content": "go"}], context=async_context)

    assert len(sync_calls) == len(async_calls) == 1
    assert sync_context.get_llm_calls()[0]["provider_invoked"] is True
    assert sync_context.get_llm_calls()[0]["provider_attempt_status"] == "attempted"
    assert sync_context.get_llm_calls()[0]["status"] == "failed"
    assert async_context.get_llm_calls()[0]["provider_invoked"] is True
    assert async_context.get_llm_calls()[0]["provider_attempt_status"] == "attempted"
    assert async_context.get_llm_calls()[0]["status"] == "cancelled"


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
        assert contexts[index].get_llm_calls()[0]["context_rollout"][
            "provider_lowering"
        ]["attribution"]["serialization"] == (
            "provider_prepared_canonical_json"
        )


@pytest.mark.asyncio
async def test_provider_verified_parity_distinguishes_all_call_shapes():
    provider, _, _ = _provider()
    model = LLMModel(
        conf=ModelConfig(
            context_compiler={"mode": "enforce", "universal_final": True}
        ),
        custom_provider=provider,
    )
    model.provider_name = "openai"
    contexts = [Context(task_id=f"shape-{index}") for index in range(4)]
    messages = [{"role": "user", "content": "same"}]

    await model.acompletion(messages, context=contexts[0])
    model.completion(messages, context=contexts[1])
    list(model.stream_completion(messages, context=contexts[2]))
    [chunk async for chunk in model.astream_completion(messages, context=contexts[3])]

    assert [
        VerifiedContextEntrypointParityReceipt.from_llm_call_record(
            context.get_llm_calls()[0]
        ).receipt.call_shape.value
        for context in contexts
    ] == ["async", "sync", "sync_stream", "async_stream"]


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


def test_prepared_receipt_mutation_failure_does_not_send_or_mark_invoked():
    class FailSecondReplacement(list):
        replacements = 0

        def __setitem__(self, index, value):
            self.replacements += 1
            if self.replacements == 2:
                raise RuntimeError("capture-replacement-failed")
            return super().__setitem__(index, value)

    class MutationFailureContext(Context):
        def __init__(self):
            super().__init__(task_id="atomic-provider-prepared")
            self.calls = FailSecondReplacement()

        def get_llm_calls(self):
            return self.calls

    provider, sync_calls, _ = _provider()
    model = _model(provider)
    context = MutationFailureContext()

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion(
            [{"role": "user", "content": "legacy"}], context=context
        )

    assert raised.value.reason_code == "provider_prepared_attempt_failed"
    assert sync_calls == []
    assert context.calls[0]["provider_invoked"] is False
    assert context.calls[0]["provider_attempt_status"] == "prepared"
    assert context._provider_cache_identity is None


def test_cache_commit_failure_rolls_back_attempt_and_does_not_send():
    class BrokenCacheContext(Context):
        def commit_provider_cache_identity(self, verified_identity):
            self._provider_cache_identity = verified_identity.identity
            raise RuntimeError("cache-commit-failed")

    provider, _, _ = _provider()
    sent = []

    class HTTPHandler:
        def sync_call(self, data, *, serialized_body=None):
            sent.append(data)
            return object()

    provider.is_http_provider = True
    provider.http_provider = HTTPHandler()
    model = LLMModel(
        conf=ModelConfig(context_compiler={"mode": "enforce", "universal_final": True}),
        custom_provider=provider,
    )
    model.provider_name = "openai"
    context = BrokenCacheContext(task_id="atomic-cache-failure")

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion(
            [{"role": "system", "content": "stable"}, {"role": "user", "content": "go"}],
            context=context,
        )

    assert raised.value.reason_code == "provider_prepared_attempt_failed"
    assert sent == []
    assert context.get_llm_calls()[0]["provider_invoked"] is False
    assert context.get_llm_calls()[0]["provider_attempt_status"] == "prepared"
    assert context._provider_cache_identity is None


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
    assert context.get_llm_calls()[0]["context_rollout"]["provider_lowering"][
        "attribution"
    ]["serialization"] == "http_serialized_canonical_json"
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
    verified_first = VerifiedContextEntrypointParityReceipt.from_llm_call_record(
        first
    )
    assert verified_first.receipt.provider_binding is not None
    assert (
        verified_first.receipt.provider_binding["serialization"]
        == "http_serialized_canonical_json"
    )
    assert first["provider_request"]["serialized_checksum"] == first[
        "provider_request"
    ]["content_hash"]
    assert first["request_trace_match"] is True
    assert second["request_trace_match"] is True
    assert first["context_observe"]["request"]["capture_stage"] == "model_boundary"
    assert first["context_rollout"]["final_compile"]["enforce"]["ready"]
    assert first["context_rollout"]["provider_lowering"][
        "cache_continuity"
    ]["status"] == "initialized"
    assert second["context_rollout"]["provider_lowering"][
        "cache_continuity"
    ]["status"] == "continued"


def test_universal_final_enforce_lowers_verified_tool_result_boundary():
    provider, sent, _ = _provider()
    model = LLMModel(
        conf=ModelConfig(
            context_compiler={"mode": "enforce", "universal_final": True}
        ),
        custom_provider=provider,
    )
    model.provider_name = "openai"
    context = Context(task_id="universal-tool-result")
    context.trace_id = ""

    model.completion(
        [
            {"role": "system", "content": "stable rules"},
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": [], "tool_calls": []},
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": [{"type": "text", "text": "untrusted output"}],
            },
        ],
        context=context,
    )

    assert len(sent) == 1
    emitted = sent[0]["messages"][3]["content"][0]["text"]
    assert "<aworld-untrusted-data" in emitted
    call = context.get_llm_calls()[0]
    assert call["context_rollout"]["final_compile"]["enforce"]["ready"] is True
    assert call["request_trace_match"] is True


def test_entrypoint_label_state_and_stale_raw_provider_receipt_are_rejected():
    provider, _, _ = _provider()
    model = LLMModel(
        conf=ModelConfig(
            context_compiler={"mode": "enforce", "universal_final": True}
        ),
        custom_provider=provider,
    )
    model.provider_name = "openai"
    context = Context(task_id="parity-forgery")
    context.trace_id = ""
    context.set_state("context_entry_point", "acp")
    context._aworld_context_entrypoint_claim = object()

    model.completion([{"role": "user", "content": "same"}], context=context)
    call = context.get_llm_calls()[0]
    receipt = ContextEntrypointParityReceipt.from_dict(
        call["context_rollout"]["entrypoint_parity"]
    )
    assert receipt.entry_point.value == "direct"
    assert receipt.label_source.value == "direct_model_boundary"

    forged = json.loads(json.dumps(call))
    forged["provider_request"]["payload"]["temperature"] = 0.5
    with pytest.raises(ValueError, match="provider request"):
        VerifiedContextEntrypointParityReceipt.from_llm_call_record(forged)

    unavailable = assess_entrypoint_parity(
        (receipt,),
        required_entry_points=("direct",),
        require_provider_bound=True,
    )
    assert unavailable["status"] == "unavailable"
    assert unavailable["reason_code"] == "entrypoint_provider_evidence_required"


def test_provider_verified_parity_preserves_model_visible_provider_parameters():
    provider, _, _ = _provider()
    model = LLMModel(
        conf=ModelConfig(
            context_compiler={"mode": "enforce", "universal_final": True}
        ),
        custom_provider=provider,
    )
    model.provider_name = "openai"
    contexts = [Context(task_id=f"provider-semantics-{index}") for index in range(2)]

    model.completion(
        [{"role": "user", "content": "same"}],
        context=contexts[0],
        reasoning_effort="low",
    )
    model.completion(
        [{"role": "user", "content": "same"}],
        context=contexts[1],
        reasoning_effort="high",
    )
    receipts = [
        VerifiedContextEntrypointParityReceipt.from_llm_call_record(
            context.get_llm_calls()[0]
        )
        for context in contexts
    ]

    assert (
        receipts[0].receipt.semantic_fingerprint
        == receipts[1].receipt.semantic_fingerprint
    )
    assert (
        receipts[0].receipt.provider_binding["provider_request_content_hash"]
        != receipts[1].receipt.provider_binding["provider_request_content_hash"]
    )
    assert receipts[0].evidence_fingerprint != receipts[1].evidence_fingerprint


def test_default_on_capability_requires_verified_provider_and_trajectory_evidence():
    provider, _, _ = _provider()
    model = LLMModel(
        conf=ModelConfig(
            context_compiler={"mode": "enforce", "universal_final": True}
        ),
        custom_provider=provider,
    )
    model.provider_name = "openai"
    context = Context(task_id="verified-capability", session_id="session")
    context.trace_id = ""
    model.completion([{"role": "user", "content": "same"}], context=context)
    verified = VerifiedContextEntrypointParityReceipt.from_llm_call_record(
        context.get_llm_calls()[0]
    )
    trajectory = TrajectoryBuildResult(
        task_id=context.task_id,
        session_id=context.session_id,
        trace_id=None,
        task_epoch=context.task_epoch,
        status=TrajectoryBuildStatus.COMPLETE,
        fidelity=TrajectoryFidelity.COMPLETE,
        reason_code=None,
        source_kind=TrajectorySourceKind.EVENT_STATE,
        source_high_watermark=1,
        scheduled_updates=0,
        completed_updates=0,
        failed_updates=0,
        pending_updates=0,
        source_agent_messages=1,
        llm_call_count=1,
        tool_call_count=0,
        persisted_items=1,
        trajectory_ref=None,
        source_checksum=None,
        trajectory_checksum="sha256:" + "1" * 64,
        builder_version="parity-test-v1",
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    lifecycle = context.context_lifecycle_state
    assert isinstance(lifecycle, ContextLifecycleState)
    capability = RolloutCapability.from_verified_evidence(
        entrypoint_receipt=verified,
        lifecycle_state=lifecycle,
        trajectory_result=trajectory,
    )
    rollback = RollbackBundle.build(
        previous_mode="shadow",
        previous_config={"mode": "shadow"},
        provider_capability_hash=canonical_json_hash(
            {"capability": capability.evidence_fingerprint}
        ),
    )
    canary_policy = CanaryHealthPolicy(
        policy_version="health-v1",
        minimum_shadow_calls=1,
        minimum_enforce_sessions=1,
        minimum_baseline_provider_attempts=1,
        minimum_enforce_provider_attempts=1,
        max_provider_error_rate_delta=0.0,
    )
    canary = assess_canary_health(
        policy=canary_policy,
        evidence=CanaryHealthEvidence(
            shadow_call_count=1,
            shadow_request_trace_match_count=1,
            shadow_provider_attribution_complete_count=1,
            enforce_session_count=1,
            enforce_provider_attempt_count=1,
            enforce_provider_error_count=0,
            baseline_provider_error_rate=0.0,
            security_violation_count=0,
            trajectory_incomplete_count=0,
            quality_regression=False,
            baseline_provider_attempt_count=1,
            baseline_provider_error_count=0,
            enforce_sessions_with_provider_attempt_count=1,
        ),
        rollback_bundle=rollback,
    )

    readiness = assess_default_on_readiness(
        capabilities=(capability,),
        required_capabilities=(("openai", "direct", "sync"),),
        workload_kinds=("terminal", "research"),
        complete_pairs=10,
        quality_regression=False,
        request_trace_match_rate=1.0,
        trajectory_complete_rate=1.0,
        rollback_config_hash=rollback.bundle_hash,
        canary_health_decision=canary,
        required_canary_policy_fingerprint=canary.policy_fingerprint,
    )

    assert capability.enforce_ready is True
    assert readiness.status is ReadinessStatus.READY


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


def test_openai_enforce_rejects_message_reorder_as_attribution_mismatch():
    provider, sync_calls, _ = _provider()
    policy = CandidateCompilePolicy(
        compiler_version="openai-lowering-test-v1",
        candidate_payload={
            "messages": [
                {"role": "user", "content": "one"},
                {"role": "user", "content": "two"},
            ],
            "tools": None,
            "params": {"temperature": 0.0, "max_tokens": 10, "stop": []},
        },
        enforce_ready=True,
    )
    model = _model(provider, policy=policy)
    context = Context(task_id="provider-attribution-reorder")
    provider.preprocess_messages = MethodType(
        lambda self, messages, **kwargs: list(reversed(messages)), provider
    )

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion(
            [{"role": "user", "content": "legacy"}], context=context
        )

    assert raised.value.reason_code == "provider_attribution_mismatch"
    assert sync_calls == []
    assert context.get_llm_calls()[0]["provider_invoked"] is False


@pytest.mark.parametrize(
    ("candidate_tools", "provider_tools_shape"),
    [(None, "absent"), ([], "array")],
)
def test_openai_declares_exact_tools_shape_lowering(
    candidate_tools, provider_tools_shape
):
    provider, sync_calls, _ = _provider()
    policy = CandidateCompilePolicy(
        compiler_version="openai-lowering-test-v1",
        candidate_payload={
            "messages": [{"role": "user", "content": "shape"}],
            "tools": candidate_tools,
            "params": {"temperature": 0.0, "max_tokens": 10, "stop": []},
        },
        enforce_ready=True,
    )
    model = _model(provider, policy=policy)
    context = Context(task_id=f"tools-shape-{provider_tools_shape}")

    model.completion([{"role": "user", "content": "legacy"}], context=context)

    assert len(sync_calls) == 1
    receipt = context.get_llm_calls()[0]["context_rollout"]["provider_lowering"]["attribution"]
    assert receipt["tools_shape"] == ("null" if candidate_tools is None else "array")
    assert receipt["provider_tools_shape"] == provider_tools_shape
    assert receipt["tools_lowering"] == "null_to_absent"
    assert ("tools" in sync_calls[0]) is (provider_tools_shape == "array")


def test_openai_rejects_dropping_an_empty_tools_array_before_send():
    provider, sync_calls, _ = _provider()
    policy = CandidateCompilePolicy(
        compiler_version="openai-lowering-test-v1",
        candidate_payload={
            "messages": [{"role": "user", "content": "shape"}],
            "tools": [],
            "params": {"temperature": 0.0, "max_tokens": 10, "stop": []},
        },
        enforce_ready=True,
    )
    original = provider.get_openai_params

    def drop_empty_tools(self, *args, **kwargs):
        params = original(*args, **kwargs)
        params.pop("tools", None)
        return params

    provider.get_openai_params = MethodType(drop_empty_tools, provider)
    context = Context(task_id="tools-shape-drop")

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        _model(provider, policy=policy).completion(
            [{"role": "user", "content": "legacy"}], context=context
        )

    assert raised.value.reason_code == "provider_attribution_mismatch"
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
