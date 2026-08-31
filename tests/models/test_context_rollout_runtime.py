from __future__ import annotations

import traceback
from typing import Any

import pytest

from aworld.config import ModelConfig
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    CandidateCompileInput,
    CandidateCompilePolicy,
    CandidateRequestNotEnforceable,
    ContextCompilerMode,
    ContextObservationSidecar,
    ProviderLoweringCapability,
    adapt_final_messages,
    canonical_json_hash,
    compile_context_candidate,
)
from aworld.core.llm_provider import LLMProviderBase
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse


class CountingProvider(LLMProviderBase):
    def __init__(self) -> None:
        super().__init__(model_name="counting-model")
        self.calls: list[tuple[str, Any, Any]] = []

    def _init_provider(self):
        return None

    def postprocess_response(self, response, **kwargs):
        return response

    def _record(self, kind: str, messages: Any, kwargs: dict[str, Any]) -> None:
        self.calls.append((kind, messages, kwargs.get("tools")))

    @staticmethod
    def _response(kind: str) -> ModelResponse:
        return ModelResponse(
            id=f"response-{kind}", model="counting-model", content=kind,
            message={"role": "assistant", "content": kind}, finish_reason="stop",
        )

    async def acompletion(self, messages, **kwargs):
        self._record("acompletion", messages, kwargs)
        return self._response("acompletion")

    def completion(self, messages, **kwargs):
        self._record("completion", messages, kwargs)
        return self._response("completion")

    def stream_completion(self, messages, **kwargs):
        self._record("stream_completion", messages, kwargs)
        yield self._response("stream_completion")

    async def astream_completion(self, messages, **kwargs):
        self._record("astream_completion", messages, kwargs)
        yield self._response("astream_completion")


def _counters() -> dict[str, int]:
    return {"compiler": 0, "tool": 0, "artifact_offload": 0}


def _candidate_policy(*, tools: Any = None) -> CandidateCompilePolicy:
    return CandidateCompilePolicy(
        compiler_version="runtime-test-v1",
        candidate_payload={
            "messages": [{"role": "user", "content": "candidate-private-content"}],
            "tools": tools,
            "params": {"temperature": 0.0, "max_tokens": None, "stop": None},
        },
        enforce_ready=True,
        diagnostic_codes=("raw-owner-diagnostic-secret",),
    )


def _model(*, mode, provider, policy=None) -> LLMModel:
    value = mode.value if isinstance(mode, ContextCompilerMode) else mode
    return LLMModel(
        conf=ModelConfig(context_compiler={
            "mode": value, "compiler_version": "runtime-test-v1"
        }),
        custom_provider=provider,
        context_candidate_policy=policy,
    )


def _count_framework_compiles(monkeypatch, counters, *, fail=False):
    inputs: list[CandidateCompileInput] = []

    def counted(*, compiler_input, policy):
        counters["compiler"] += 1
        inputs.append(compiler_input)
        if fail:
            raise ValueError("raw-compiler-failure-secret")
        return compile_context_candidate(compiler_input=compiler_input, policy=policy)

    monkeypatch.setattr("aworld.models.llm.compile_context_candidate", counted)
    return inputs


@pytest.mark.asyncio
async def test_shadow_compiles_once_per_path_and_executes_only_legacy(monkeypatch):
    counters = _counters()
    inputs = _count_framework_compiles(monkeypatch, counters)
    provider = CountingProvider()
    messages = [{"role": "user", "content": "legacy-content"}]
    tools = [{"type": "function", "function": {"name": "legacy-tool"}}]
    model = _model(mode="shadow", provider=provider, policy=_candidate_policy(tools=tools))
    contexts = [Context(task_id=f"shadow-{index}") for index in range(4)]
    sidecar = ContextObservationSidecar.from_adapter_result(
        owner="amni.neuron_outputs", namespace="agent-runtime",
        source_identity="owner://private/runtime/path",
        result=adapt_final_messages(
            [{"role": "system", "content": "private-owner-observation"}],
            source_identity="owner://private/runtime/path",
        ),
    )
    contexts[0].publish_context_observation(sidecar)

    await model.acompletion(messages, context=contexts[0], tools=tools)
    model.completion(messages, context=contexts[1], tools=tools)
    list(model.stream_completion(messages, context=contexts[2], tools=tools))
    async_chunks = [chunk async for chunk in model.astream_completion(
        messages, context=contexts[3], tools=tools
    )]

    assert len(async_chunks) == 1
    assert counters == {"compiler": 4, "tool": 0, "artifact_offload": 0}
    assert inputs[0].observations == (sidecar,)
    assert all(not item.observations for item in inputs[1:])
    assert [kind for kind, _, _ in provider.calls] == [
        "acompletion", "completion", "stream_completion", "astream_completion"
    ]
    assert all(seen is messages for _, seen, _ in provider.calls)
    assert all(seen is tools for _, _, seen in provider.calls)
    for context in contexts:
        rollout = context.get_llm_calls()[0]["context_rollout"]
        assert rollout["compiler_identity"] == "aworld.context.compiler.framework"
        assert rollout["compiler_version"] == "runtime-test-v1"
        assert rollout["comparison_projection"] == "aworld.standard.model_boundary.v1"
        assert rollout["comparison_direction"] == "candidate_against_legacy"
        assert rollout["external_actions_authorized"] is False
        assert rollout["external_action_count_observed"] is None
        assert rollout["provider_lowering_ready"] is False
        assert rollout["candidate_snapshot"]["content_hash"]
        assert rollout["legacy_snapshot"]["content_hash"]
        assert rollout["candidate_snapshot"]["fidelity"] == "model_boundary"
        assert rollout["comparison"]["mismatch_paths"] == ["/messages/0/content"]
        assert rollout["compiler_elapsed_ms"] >= 0
        assert rollout["diagnostic_code_hashes"] == [
            canonical_json_hash({"code": "raw-owner-diagnostic-secret"})
        ]
        rendered = repr(rollout)
        for secret in (
            "candidate-private-content", "raw-owner-diagnostic-secret",
            "private-owner-observation", "owner://private/runtime/path",
        ):
            assert secret not in rendered


@pytest.mark.asyncio
async def test_shadow_framework_failure_is_redacted_and_fails_open(monkeypatch):
    counters = _counters()
    _count_framework_compiles(monkeypatch, counters, fail=True)
    provider = CountingProvider()
    model = _model(mode="shadow", provider=provider)
    messages = [{"role": "user", "content": "legacy-content"}]
    context = Context(task_id="shadow-fail-open")

    await model.acompletion(messages, context=context)

    assert counters["compiler"] == 1
    assert provider.calls[0][1] is messages
    rollout = context.get_llm_calls()[0]["context_rollout"]
    assert rollout["candidate_status"] == "failed"
    assert rollout["error"] == {"code": "candidate_compilation_failed"}
    assert "raw-compiler-failure-secret" not in repr(rollout)


@pytest.mark.asyncio
async def test_shadow_invalid_snapshot_input_fails_open_before_compile(monkeypatch):
    counters = _counters()
    _count_framework_compiles(monkeypatch, counters)
    provider = CountingProvider()
    model = _model(mode="shadow", provider=provider)
    messages = [{"role": "user", "content": float("nan")}]
    context = Context(task_id="shadow-invalid-input")

    await model.acompletion(messages, context=context)

    assert counters["compiler"] == 0
    assert provider.calls[0][1] is messages
    assert context.get_llm_calls()[0]["context_rollout"]["error"] == {
        "code": "candidate_input_failed"
    }


def test_enforce_records_blocked_before_provider_with_candidate_evidence():
    provider = CountingProvider()
    model = _model(mode="enforce", provider=provider, policy=_candidate_policy())
    context = Context(task_id="enforce-blocked")

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion([{"role": "user", "content": "legacy-content"}], context=context)

    assert raised.value.reason_code == "provider_lowering_required"
    assert provider.calls == []
    record = context.get_llm_calls()[0]
    assert record["status"] == "blocked_before_provider"
    assert record["provider_invoked"] is False
    assert record["provider_request_id"] is None
    assert record["context_rollout"]["candidate_snapshot"]["content_hash"]
    assert record["context_rollout"]["error"] == {"code": "provider_lowering_required"}


def test_enforce_compile_failure_is_redacted_and_recorded(monkeypatch):
    counters = _counters()
    _count_framework_compiles(monkeypatch, counters, fail=True)
    provider = CountingProvider()
    model = _model(mode="enforce", provider=provider)
    context = Context(task_id="enforce-compiler-failed")

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion([{"role": "user", "content": "legacy-content"}], context=context)

    rendered = "".join(traceback.format_exception(
        type(raised.value), raised.value, raised.value.__traceback__
    ))
    assert raised.value.reason_code == "compiler_failed"
    assert "raw-compiler-failure-secret" not in rendered
    assert provider.calls == []
    assert context.get_llm_calls()[0]["status"] == "blocked_before_provider"
    assert context.get_llm_calls()[0]["provider_invoked"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "observe"])
async def test_off_and_observe_never_compile_candidate(monkeypatch, mode):
    counters = _counters()
    _count_framework_compiles(monkeypatch, counters)
    provider = CountingProvider()
    model = _model(mode=mode, provider=provider, policy=_candidate_policy())
    messages = [{"role": "user", "content": "legacy-content"}]
    tools = [{"type": "function", "function": {"name": "legacy-tool"}}]
    context = Context(task_id=f"mode-{mode}")

    await model.acompletion(messages, context=context, tools=tools)

    assert counters == {"compiler": 0, "tool": 0, "artifact_offload": 0}
    assert provider.calls[0][1] is messages
    assert provider.calls[0][2] is tools
    record = context.get_llm_calls()[0]
    if mode == "off":
        assert "context_rollout" not in record
    else:
        assert record["context_rollout"]["candidate_status"] == "not_requested"


def test_runtime_rejects_arbitrary_compiler_object_before_it_can_act():
    counters = _counters()

    class AmbientCapabilityCompiler:
        def compile_candidate(self, **kwargs):
            counters["tool"] += 1

    with pytest.raises(TypeError):
        LLMModel(
            custom_provider=CountingProvider(),
            context_candidate_policy=AmbientCapabilityCompiler(),
        )

    model = _model(mode="shadow", provider=CountingProvider())
    with pytest.raises(AttributeError):
        model.context_candidate_policy = AmbientCapabilityCompiler()
    with pytest.raises(AttributeError):
        model.context_compiler_mode = "enforce"

    assert counters == {"compiler": 0, "tool": 0, "artifact_offload": 0}


def test_custom_provider_cannot_self_authorize_enforce_lowering():
    class SelfAuthorizingProvider(CountingProvider):
        def context_candidate_lowering_capability(self):
            return ProviderLoweringCapability(
                provider_name="custom",
                adapter_identity="untrusted.custom.provider",
                adapter_version="v1",
                request_projection="custom.request.v1",
            )

    provider = SelfAuthorizingProvider()
    model = _model(mode="enforce", provider=provider, policy=_candidate_policy())
    context = Context(task_id="self-authorizing-provider")

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion(
            [{"role": "user", "content": "legacy"}], context=context
        )

    assert raised.value.reason_code == "provider_lowering_required"
    assert provider.calls == []
    assert context.get_llm_calls()[0]["provider_invoked"] is False
