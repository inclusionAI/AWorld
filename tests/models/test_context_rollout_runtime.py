from __future__ import annotations

import hashlib
import json
import traceback
from types import MethodType, SimpleNamespace
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
from aworld.models.anthropic_provider import AnthropicProvider
from aworld.models.ant_provider import AntProvider
from aworld.models.openai_provider import AzureOpenAIProvider
from aworld.models.reviewed_custom_provider import ReviewedCustomChatProvider


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
            id=f"response-{kind}",
            model="counting-model",
            content=kind,
            message={"role": "assistant", "content": kind},
            finish_reason="stop",
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


def _system_and_tool_candidate_policy() -> CandidateCompilePolicy:
    return CandidateCompilePolicy(
        compiler_version="runtime-test-v1",
        candidate_payload={
            "messages": [
                {"role": "system", "content": "stable-system"},
                {"role": "user", "content": "candidate-user"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read a file",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
            "params": {"temperature": 0.0, "max_tokens": None, "stop": None},
        },
        enforce_ready=True,
    )


def _model(*, mode, provider, policy=None) -> LLMModel:
    value = mode.value if isinstance(mode, ContextCompilerMode) else mode
    return LLMModel(
        conf=ModelConfig(
            context_compiler={"mode": value, "compiler_version": "runtime-test-v1"}
        ),
        custom_provider=provider,
        context_candidate_policy=policy,
    )


def _azure_without_transport() -> tuple[AzureOpenAIProvider, list[str]]:
    """Build the exact built-in type without credentials or a real transport."""
    provider = object.__new__(AzureOpenAIProvider)
    provider.model_name = "azure-test"
    provider.kwargs = {}
    calls: list[str] = []
    sent_params: list[dict[str, Any]] = []

    class SyncCompletions:
        def create(self, **kwargs):
            calls.append("stream_completion" if kwargs.get("stream") else "completion")
            sent_params.append(kwargs)
            return [object()] if kwargs.get("stream") else object()

    class AsyncCompletions:
        async def create(self, **kwargs):
            calls.append(
                "astream_completion" if kwargs.get("stream") else "acompletion"
            )
            sent_params.append(kwargs)
            if not kwargs.get("stream"):
                return object()

            async def chunks():
                yield object()

            return chunks()

    provider.provider = SimpleNamespace(
        chat=SimpleNamespace(completions=SyncCompletions())
    )
    provider.async_provider = SimpleNamespace(
        chat=SimpleNamespace(completions=AsyncCompletions())
    )
    provider.is_http_provider = False
    provider.stream_tool_buffer = []
    provider._test_sent_params = sent_params
    provider.postprocess_response = MethodType(
        lambda self, response: CountingProvider._response("completion"), provider
    )
    provider.postprocess_stream_response = MethodType(
        lambda self, chunk: (CountingProvider._response("stream"), "stop"),
        provider,
    )
    return provider, calls


def _anthropic_without_transport() -> tuple[AnthropicProvider, list[dict[str, Any]]]:
    provider = object.__new__(AnthropicProvider)
    provider.model_name = "claude-test"
    provider.kwargs = {}
    calls: list[dict[str, Any]] = []

    class SyncMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return [object()] if kwargs.get("stream") else object()

    class AsyncMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            if not kwargs.get("stream"):
                return object()

            async def chunks():
                yield object()

            return chunks()

    provider.provider = SimpleNamespace(messages=SyncMessages())
    provider.async_provider = SimpleNamespace(messages=AsyncMessages())
    provider.stream_tool_buffer = []
    provider.postprocess_response = MethodType(
        lambda self, response: CountingProvider._response("anthropic"), provider
    )
    provider.postprocess_stream_response = MethodType(
        lambda self, chunk: CountingProvider._response("anthropic-stream"),
        provider,
    )
    return provider, calls


def _ant_without_transport() -> tuple[AntProvider, list[dict[str, Any]]]:
    provider = object.__new__(AntProvider)
    provider.model_name = "gpt-test"
    provider.api_key = "test-key"
    provider.aes_key = "not-used"
    provider.stream_api_key = "test-stream-key"
    provider.kwargs = {
        "ant_visit_biz": "test-biz",
        "ant_visit_biz_line": "test-line",
    }
    provider.stream_tool_buffer = []
    calls: list[dict[str, Any]] = []

    class HTTP:
        def sync_call(self, payload, **kwargs):
            calls.append(payload)
            return {}

        async def async_call(self, payload, **kwargs):
            calls.append(payload)
            return {}

        def sync_stream_call(self, payload, **kwargs):
            calls.append(payload)
            yield object()

        async def async_stream_call(self, payload, **kwargs):
            calls.append(payload)
            yield object()

    provider.http_provider = HTTP()
    provider.provider = provider.http_provider
    provider.async_provider = provider.http_provider

    def opaque_request(self, payload):
        encoded = json.dumps(payload, sort_keys=True, default=str).encode()
        return {
            "encryptedParam": (
                "sha256:"
                + hashlib.sha256(encoded).hexdigest()
                + "0" * (len(encoded) * 2)
            )
        }

    provider._build_request_data = MethodType(opaque_request, provider)
    provider._pull_chat_result = MethodType(
        lambda self, message_key, response, timeout: object(), provider
    )

    async def async_pull(self, message_key, response, timeout):
        return object()

    provider._async_pull_chat_result = MethodType(async_pull, provider)
    provider.postprocess_response = MethodType(
        lambda self, response: CountingProvider._response("ant"), provider
    )
    provider.postprocess_stream_response = MethodType(
        lambda self, response: CountingProvider._response("ant-stream"), provider
    )
    return provider, calls


def _reviewed_custom_provider() -> tuple[
    ReviewedCustomChatProvider, list[dict[str, Any]]
]:
    calls: list[dict[str, Any]] = []

    class Transport:
        @staticmethod
        def response(kind):
            return CountingProvider._response(kind)

        def completion(self, payload):
            calls.append(payload)
            return self.response("custom-completion")

        async def acompletion(self, payload):
            calls.append(payload)
            return self.response("custom-acompletion")

        def stream_completion(self, payload):
            calls.append(payload)
            yield self.response("custom-stream")

        async def astream_completion(self, payload):
            calls.append(payload)
            yield self.response("custom-astream")

    return (
        ReviewedCustomChatProvider(transport=Transport(), model_name="custom-test"),
        calls,
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
    model = _model(
        mode="shadow", provider=provider, policy=_candidate_policy(tools=tools)
    )
    contexts = [Context(task_id=f"shadow-{index}") for index in range(4)]
    sidecar = ContextObservationSidecar.from_adapter_result(
        owner="amni.neuron_outputs",
        namespace="agent-runtime",
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
    async_chunks = [
        chunk
        async for chunk in model.astream_completion(
            messages, context=contexts[3], tools=tools
        )
    ]

    assert len(async_chunks) == 1
    assert counters == {"compiler": 4, "tool": 0, "artifact_offload": 0}
    assert inputs[0].observations[0] == sidecar
    assert {item.owner for item in inputs[0].observations[1:]} == {
        "model.final_messages",
        "model.final_tool_catalog",
    }
    assert all(
        {sidecar.owner for sidecar in item.observations}
        == {"model.final_messages", "model.final_tool_catalog"}
        for item in inputs[1:]
    )
    assert [kind for kind, _, _ in provider.calls] == [
        "acompletion",
        "completion",
        "stream_completion",
        "astream_completion",
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
            "candidate-private-content",
            "raw-owner-diagnostic-secret",
            "private-owner-observation",
            "owner://private/runtime/path",
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
        model.completion(
            [{"role": "user", "content": "legacy-content"}], context=context
        )

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
        model.completion(
            [{"role": "user", "content": "legacy-content"}], context=context
        )

    rendered = "".join(
        traceback.format_exception(
            type(raised.value), raised.value, raised.value.__traceback__
        )
    )
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
        model.completion([{"role": "user", "content": "legacy"}], context=context)

    assert raised.value.reason_code == "provider_lowering_required"
    assert provider.calls == []
    assert context.get_llm_calls()[0]["provider_invoked"] is False


@pytest.mark.asyncio
async def test_exact_azure_provider_lowers_candidate_across_all_enforce_send_paths():
    provider, calls = _azure_without_transport()
    model = _model(mode="enforce", provider=provider, policy=_candidate_policy())
    model.provider_name = "azure_openai"
    contexts = [Context(task_id=f"azure-blocked-{index}") for index in range(4)]
    messages = [{"role": "user", "content": "legacy"}]

    await model.acompletion(messages, context=contexts[0])
    model.completion(messages, context=contexts[1])
    list(model.stream_completion(messages, context=contexts[2]))
    [chunk async for chunk in model.astream_completion(messages, context=contexts[3])]

    assert calls == [
        "acompletion",
        "completion",
        "stream_completion",
        "astream_completion",
    ]
    assert all(
        params["messages"] == [{"role": "user", "content": "candidate-private-content"}]
        for params in provider._test_sent_params
    )
    for context in contexts:
        record = context.get_llm_calls()[0]
        assert record["status"] == "success"
        assert record["provider_invoked"] is True
        assert record["provider_attempt_status"] == "attempted"
        assert record["request_selection"] == "candidate"
        assert record["request"]["messages"] == [
            {"role": "user", "content": "candidate-private-content"}
        ]
        lowering = record["context_rollout"]["provider_lowering"]
        assert lowering["adapter_identity"] == (
            "aworld.provider.azure_openai.chat_completions"
        )
        assert record["provider_request"]["provider_name"] == "azure_openai"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "observe"])
async def test_exact_azure_provider_preserves_legacy_request_outside_enforce(mode):
    provider, calls = _azure_without_transport()
    model = _model(mode=mode, provider=provider, policy=_candidate_policy())
    model.provider_name = "azure_openai"
    contexts = [Context(task_id=f"azure-{mode}-{index}") for index in range(4)]
    messages = [{"role": "user", "content": "legacy"}]

    await model.acompletion(messages, context=contexts[0])
    model.completion(messages, context=contexts[1])
    list(model.stream_completion(messages, context=contexts[2]))
    [chunk async for chunk in model.astream_completion(messages, context=contexts[3])]

    assert calls == [
        "acompletion",
        "completion",
        "stream_completion",
        "astream_completion",
    ]
    for context in contexts:
        record = context.get_llm_calls()[0]
        assert record["status"] == "success"
        # Legacy-compatible records omit the field when the provider was
        # invoked; only a blocked/not-yet-attempted call writes ``False``.
        assert record.get("provider_invoked", True) is True
        assert record["request"]["messages"] == messages
        if mode == "off":
            assert "context_rollout" not in record
        else:
            rollout = record["context_rollout"]
            assert rollout["candidate_status"] == "not_requested"
            assert rollout["provider_attribution"]["subject"] == "legacy_observed"
            assert rollout["provider_attribution"]["status"] == "available"
            assert rollout["provider_attribution"]["adapter_identity"] == (
                "aworld.provider.azure_openai.chat_completions"
            )


@pytest.mark.asyncio
async def test_exact_anthropic_provider_lowers_candidate_across_all_send_paths():
    provider, calls = _anthropic_without_transport()
    model = _model(mode="enforce", provider=provider, policy=_candidate_policy())
    model.provider_name = "anthropic"
    contexts = [Context(task_id=f"anthropic-{index}") for index in range(4)]
    messages = [{"role": "user", "content": "legacy"}]

    await model.acompletion(messages, context=contexts[0])
    model.completion(messages, context=contexts[1])
    list(model.stream_completion(messages, context=contexts[2]))
    [chunk async for chunk in model.astream_completion(messages, context=contexts[3])]

    assert [call.get("stream", False) for call in calls] == [
        False,
        False,
        True,
        True,
    ]
    assert all(
        call["messages"] == [{"role": "user", "content": "candidate-private-content"}]
        for call in calls
    )
    for context in contexts:
        record = context.get_llm_calls()[0]
        assert record["status"] == "success"
        assert record["provider_invoked"] is True
        assert record["request_selection"] == "candidate"
        assert record["provider_request"]["provider_name"] == "anthropic"
        assert (
            record["context_rollout"]["provider_lowering"]["adapter_identity"]
            == "aworld.provider.anthropic.messages"
        )


@pytest.mark.asyncio
async def test_exact_ant_provider_lowers_candidate_across_all_send_paths():
    provider, calls = _ant_without_transport()
    model = _model(mode="enforce", provider=provider, policy=_candidate_policy())
    model.provider_name = "ant"
    contexts = [Context(task_id=f"ant-{index}") for index in range(4)]
    messages = [{"role": "user", "content": "legacy"}]

    await model.acompletion(messages, context=contexts[0])
    model.completion(messages, context=contexts[1])
    list(model.stream_completion(messages, context=contexts[2]))
    [chunk async for chunk in model.astream_completion(messages, context=contexts[3])]

    assert len(calls) == 4
    assert all(
        call.get("encryptedParam")
        or call["messages"]
        == [{"role": "user", "content": "candidate-private-content"}]
        for call in calls
    )
    for context in contexts:
        record = context.get_llm_calls()[0]
        assert record["status"] == "success"
        assert record["provider_invoked"] is True
        assert record["request_selection"] == "candidate"
        assert record["provider_request"]["provider_name"] == "ant"
        assert (
            record["context_rollout"]["provider_lowering"]["adapter_identity"]
            == "aworld.provider.ant.chat"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "provider_name", "adapter_identity"),
    [
        (
            _anthropic_without_transport,
            "anthropic",
            "aworld.provider.anthropic.messages",
        ),
        (_ant_without_transport, "ant", "aworld.provider.ant.chat"),
    ],
)
async def test_non_openai_builtins_observe_the_legacy_provider_projection(
    factory, provider_name, adapter_identity
):
    provider, _ = factory()
    model = _model(mode="observe", provider=provider, policy=_candidate_policy())
    model.provider_name = provider_name
    context = Context(task_id=f"{provider_name}-observe")

    await model.acompletion([{"role": "user", "content": "legacy"}], context=context)

    record = context.get_llm_calls()[0]
    attribution = record["context_rollout"]["provider_attribution"]
    assert record["status"] == "success"
    assert record["provider_invoked"] is True
    assert record["request"]["messages"] == [{"role": "user", "content": "legacy"}]
    assert attribution["status"] == "available"
    assert attribution["subject"] == "legacy_observed"
    assert attribution["adapter_identity"] == adapter_identity


def test_anthropic_enforce_projects_system_and_tool_collections_at_send_boundary():
    provider, calls = _anthropic_without_transport()
    model = _model(
        mode="enforce",
        provider=provider,
        policy=_system_and_tool_candidate_policy(),
    )
    model.provider_name = "anthropic"
    context = Context(task_id="anthropic-system-tool")

    model.completion([{"role": "user", "content": "legacy"}], context=context)

    assert calls[0]["system"] == "stable-system"
    assert calls[0]["messages"] == [{"role": "user", "content": "candidate-user"}]
    assert calls[0]["tools"] == [
        {
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]
    assert context.get_llm_calls()[0]["status"] == "success"


def test_ant_enforce_binds_encrypted_transport_without_persisting_credentials():
    provider, calls = _ant_without_transport()
    model = _model(
        mode="enforce",
        provider=provider,
        policy=_system_and_tool_candidate_policy(),
    )
    model.provider_name = "ant"
    context = Context(task_id="ant-encrypted-provider-boundary")

    model.completion([{"role": "user", "content": "legacy"}], context=context)

    assert list(calls[0]) == ["encryptedParam"]
    record = context.get_llm_calls()[0]
    assert record["status"] == "success"
    assert record["provider_request"]["payload"] == calls[0]
    assert "test-key" not in repr(record["provider_request"])


@pytest.mark.asyncio
async def test_framework_owned_custom_transport_has_reviewed_four_path_parity():
    provider, calls = _reviewed_custom_provider()
    model = _model(mode="enforce", provider=provider, policy=_candidate_policy())
    # LLMModel intentionally assigns this exact wrapper to the custom provider
    # namespace; arbitrary subclasses are still rejected by the registry.
    assert model.provider_name == "custom"
    contexts = [Context(task_id=f"custom-reviewed-{index}") for index in range(4)]
    messages = [{"role": "user", "content": "legacy"}]

    await model.acompletion(messages, context=contexts[0])
    model.completion(messages, context=contexts[1])
    list(model.stream_completion(messages, context=contexts[2]))
    [chunk async for chunk in model.astream_completion(messages, context=contexts[3])]

    assert len(calls) == 4
    assert all(
        call["messages"] == [{"role": "user", "content": "candidate-private-content"}]
        for call in calls
    )
    for context in contexts:
        record = context.get_llm_calls()[0]
        assert record["status"] == "success"
        assert record["provider_invoked"] is True
        assert record["provider_request"]["provider_name"] == "custom"
        assert (
            record["context_rollout"]["provider_lowering"]["adapter_identity"]
            == "aworld.provider.custom.standard_chat"
        )


def test_reviewed_custom_wrapper_subclass_cannot_inherit_authorization():
    provider, _ = _reviewed_custom_provider()

    class UnreviewedSubclass(ReviewedCustomChatProvider):
        pass

    unreviewed = UnreviewedSubclass(
        transport=provider.provider, model_name="custom-subclass"
    )
    model = _model(mode="enforce", provider=unreviewed, policy=_candidate_policy())
    context = Context(task_id="custom-wrapper-subclass")

    with pytest.raises(CandidateRequestNotEnforceable) as raised:
        model.completion([{"role": "user", "content": "legacy"}], context=context)

    assert raised.value.reason_code == "provider_lowering_required"
