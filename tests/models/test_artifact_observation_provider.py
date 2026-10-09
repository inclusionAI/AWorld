from __future__ import annotations

import base64
import json
from copy import deepcopy
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

import aworld.sandbox.artifact_observation as artifact_module
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    CandidateCompilePolicy,
    ProviderLoweringCapability,
    ProviderRequestFidelity,
    VerifiedContextEntrypointParityReceipt,
)
from aworld.core.context.amni.prompt.assembly.provider import (
    DefaultPromptAssemblyProvider,
)
from aworld.core.llm_provider import LLMProviderBase
from aworld.core.task import Task
from aworld.agents.llm_agent import (
    Agent,
    LlmOutputParser,
    ToolCallBatchParseError,
    ToolCallParseIssueCode,
)
from aworld.config import AgentConfig, AgentMemoryConfig, ModelConfig
from aworld.core.context.generation_budget import GenerationBudgetPolicy
from aworld.core.context.session import Session
from aworld.core.event.base import Constants, Message
from aworld.mcp_client.utils import lower_mcp_call_result
from aworld.models.llm import LLMModel
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.models.anthropic_provider import AnthropicProvider
from aworld.models.openai_provider import OpenAIProvider
from aworld.models.openai_message_sanitizer import sanitize_openai_messages
from aworld.models.provider_context_request import (
    ProviderWireProjection,
    prepare_provider_context_request,
)
from aworld.models.provider_media import (
    ANTHROPIC_MEDIA_PROJECTION,
    OPENAI_MEDIA_PROJECTION,
    stage_provider_media_audit,
)
from aworld.runners.handler.memory import DefaultMemoryHandler
from aworld.trace.instrumentation.openai.inout_parse import (
    handle_openai_request,
    record_stream_response_chunk,
    record_stream_token_usage,
)
from aworld.sandbox.artifact_observation import (
    ARTIFACT_RETAINED_MESSAGE,
    artifact_memory_descriptor,
    artifact_mcp_content,
    artifact_prompt_message,
    clear_artifact_observation_state,
    commit_artifact_projection,
    hydrate_artifact_messages,
    mark_artifact_rollout_late_bound,
    observe_artifact_bytes,
)


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4"
    "z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
_FILE_EPOCH = "sha256:" + ("0" * 64)


class _CapturingProvider(LLMProviderBase):
    def __init__(self) -> None:
        super().__init__(model_name="vision-test")
        self.calls: list[list[dict[str, Any]]] = []
        self.kwargs_calls: list[dict[str, Any]] = []

    def _init_provider(self):
        return None

    def postprocess_response(self, response, **kwargs):
        return response

    @staticmethod
    def _response() -> ModelResponse:
        return ModelResponse(
            id="response-1",
            model="vision-test",
            content="inspected",
            message={"role": "assistant", "content": "inspected"},
            finish_reason="stop",
        )

    async def acompletion(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs_calls.append(dict(kwargs))
        return self._response()

    def completion(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs_calls.append(dict(kwargs))
        return self._response()

    def provider_media_projection_capability(self):
        return OPENAI_MEDIA_PROJECTION

    async def astream_completion(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs_calls.append(dict(kwargs))
        yield self._response()


class _Memory:
    def __init__(self) -> None:
        self.items = []

    async def add(self, item, agent_memory_config=None):
        self.items.append(item)

    def get_all(self, filters=None):
        return []


class _Agent:
    memory_config = AgentMemoryConfig()

    @staticmethod
    def id() -> str:
        return "agent-1"

    @staticmethod
    def name() -> str:
        return "Aworld"


class _PartialFailProvider(_CapturingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.stream_attempts = 0

    async def astream_completion(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs_calls.append(dict(kwargs))
        self.stream_attempts += 1
        yield self._response()
        if self.stream_attempts == 1:
            raise RuntimeError("partial stream failure")


class _SyncPartialFailProvider(_CapturingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.stream_attempts = 0

    def stream_completion(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs_calls.append(dict(kwargs))
        self.stream_attempts += 1
        yield self._response()
        if self.stream_attempts == 1:
            raise RuntimeError("partial sync stream failure")


class _EmptyThenSuccessProvider(_CapturingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.stream_attempts = 0

    async def astream_completion(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs_calls.append(dict(kwargs))
        self.stream_attempts += 1
        if self.stream_attempts == 1:
            return
        yield self._response()


class _AlwaysEmptyStreamProvider(_CapturingProvider):
    async def astream_completion(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs_calls.append(dict(kwargs))
        if False:
            yield self._response()


class _UnfinishedThenSuccessProvider(_CapturingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.stream_attempts = 0

    async def astream_completion(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs_calls.append(dict(kwargs))
        self.stream_attempts += 1
        if self.stream_attempts == 1:
            response = self._response()
            response.finish_reason = None
            yield response
            return
        yield self._response()


class _NoneThenSuccessProvider(_CapturingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def acompletion(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs_calls.append(dict(kwargs))
        self.attempts += 1
        return None if self.attempts == 1 else self._response()


class _AnthropicCapturingProvider(_CapturingProvider):
    def provider_media_projection_capability(self):
        return ANTHROPIC_MEDIA_PROJECTION


class _UnsupportedMediaProvider(_CapturingProvider):
    def provider_media_projection_capability(self):
        return None


class _FailOnceParser:
    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, response, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("parser rejected response")
        return response


class _NoneThenSuccessParser:
    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, response, **kwargs):
        self.calls += 1
        return None if self.calls == 1 else response


def _causal_messages(
    context: Context,
    call_id: str = "call-1",
    path_key: str = "sha256:path",
):
    observed = observe_artifact_bytes(
        _PNG_1X1,
        suffix=".png",
        path_key=path_key,
        file_epoch=_FILE_EPOCH,
        framework_scope={
            "task_id": context.task_id,
            "session_id": context.session_id,
            "task_epoch": context.task_epoch,
            "tool_call_id": call_id,
        },
    )
    result = lower_mcp_call_result(
        CallToolResult(content=artifact_mcp_content(observed)),
        server_name="terminal",
        tool_name="observe_artifact",
        trusted_artifact_observation=True,
    )
    result.tool_call_id = call_id
    descriptor = artifact_memory_descriptor(
        result, context=context, tool_call_id=call_id
    )
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "observe_artifact",
                        "arguments": '{"path":"pixel.png"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": result.content},
        artifact_prompt_message([descriptor]),
    ]


@pytest.fixture(autouse=True)
def _reset_sidecars() -> None:
    clear_artifact_observation_state()


@pytest.mark.asyncio
async def test_provider_receives_causal_image_once_without_persisting_base64() -> None:
    context = Context(task_id="artifact-task")
    context.set_task(
        Task(
            id="artifact-task",
            session_id=context.session_id,
            user_id="user-1",
            input="inspect",
        )
    )
    context.trace_id = ""
    messages = _causal_messages(context)
    provider = _CapturingProvider()
    model = LLMModel(custom_provider=provider)
    encoded = base64.b64encode(_PNG_1X1).decode("ascii")

    await model.acompletion(
        messages,
        context=context,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )

    sent = provider.calls[0]
    assert [item["role"] for item in sent[-3:]] == ["assistant", "tool", "user"]
    assert sent[-2]["tool_call_id"] == "call-1"
    assert sent[-1]["content"][1]["image_url"]["url"] == (
        f"data:image/png;base64,{encoded}"
    )
    retained = json.dumps(context.get_llm_calls(), ensure_ascii=False, default=str)
    assert encoded not in retained
    assert "data:image" not in retained
    rollout = mark_artifact_rollout_late_bound(
        {"candidate_applied": True, "candidate_status": "compiled"}
    )
    assert rollout["candidate_applied"] is True
    assert rollout["candidate_status"] == "compiled"
    assert rollout["artifact_observation"]["provider_cache_eligible"] is True

    # Checkpoint compaction does not reactivate a delivered image. A new Tool
    # observation (and therefore a new call hash) is required.
    context.advance_context_lifecycle("checkpoint")
    assert [
        item
        async for item in model.astream_completion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
    ]
    assert provider.calls[-1][-1]["content"] == ARTIFACT_RETAINED_MESSAGE


@pytest.mark.asyncio
async def test_provider_without_context_degrades_reference_instead_of_leaking_uri() -> (
    None
):
    owner = Context(task_id="artifact-task")
    messages = _causal_messages(owner)
    provider = _CapturingProvider()
    model = LLMModel(custom_provider=provider)

    await model.acompletion(
        messages,
        context=None,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )

    serialized = json.dumps(provider.calls[0])
    assert "aworld-artifact://" not in serialized
    assert "data:image" not in serialized
    assert ARTIFACT_RETAINED_MESSAGE in serialized


@pytest.mark.asyncio
async def test_anthropic_capability_projects_native_base64_source_block() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    provider = _AnthropicCapturingProvider()
    model = LLMModel(custom_provider=provider)

    await model.acompletion(
        messages,
        context=context,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )

    blocks = provider.calls[0][-1]["content"]
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["type"] == "base64"
    assert blocks[1]["source"]["media_type"] == "image/png"
    assert "image_url" not in json.dumps(blocks)


@pytest.mark.asyncio
async def test_real_openai_lowering_records_redacted_media_fidelity() -> None:
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return object()

    provider = object.__new__(OpenAIProvider)
    provider.model_name = "gpt-test"
    provider.kwargs = {}
    provider.base_url = None
    provider.provider = None
    provider.async_provider = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    provider.is_http_provider = False
    provider.stream_tool_buffer = []
    provider.postprocess_response = MethodType(
        lambda self, response: _CapturingProvider._response(), provider
    )
    context = Context(task_id="artifact-openai")
    context.trace_id = ""
    messages = _causal_messages(context)
    model = LLMModel(custom_provider=provider)

    await model.acompletion(
        messages,
        context=context,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )

    assert "data:image/png;base64," in json.dumps(calls[0])
    records = json.dumps(context.get_llm_calls(), ensure_ascii=False, default=str)
    assert "data:image" not in records
    provider_snapshot = next(
        call["provider_request"]
        for call in context.get_llm_calls()
        if isinstance(call, dict) and isinstance(call.get("provider_request"), dict)
    )
    assert provider_snapshot["fidelity"] == "provider_prepared_media_redacted"


@pytest.mark.asyncio
async def test_enforce_media_preserves_candidate_not_legacy_sync_and_async() -> None:
    sync_calls: list[dict[str, Any]] = []
    async_calls: list[dict[str, Any]] = []

    class SyncCompletions:
        def create(self, **kwargs):
            sync_calls.append(kwargs)
            return object()

    class AsyncCompletions:
        async def create(self, **kwargs):
            async_calls.append(kwargs)
            return object()

    async def run_call(*, asynchronous: bool) -> Context:
        context = Context(task_id=f"artifact-enforce-{'async' if asynchronous else 'sync'}")
        context.trace_id = ""
        legacy_messages = [
            {"role": "system", "content": "LEGACY-STABLE"},
            *_causal_messages(context),
        ]
        candidate_messages = deepcopy(legacy_messages)
        candidate_messages[0]["content"] = "CANDIDATE-STABLE"
        candidate_messages = sanitize_openai_messages(candidate_messages)
        provider = object.__new__(OpenAIProvider)
        provider.model_name = "gpt-test"
        provider.kwargs = {}
        provider.base_url = None
        provider.provider = SimpleNamespace(
            chat=SimpleNamespace(completions=SyncCompletions())
        )
        provider.async_provider = SimpleNamespace(
            chat=SimpleNamespace(completions=AsyncCompletions())
        )
        provider.is_http_provider = False
        provider.stream_tool_buffer = []
        provider.postprocess_response = MethodType(
            lambda self, response: _CapturingProvider._response(), provider
        )
        policy = CandidateCompilePolicy(
            compiler_version="artifact-media-enforce-v1",
            candidate_payload={
                "messages": candidate_messages,
                "tools": None,
                "params": {
                    "temperature": 0,
                    "max_tokens": 128,
                    "stop": None,
                },
            },
            enforce_ready=True,
        )
        model = LLMModel(
            conf=ModelConfig(
                context_compiler={
                    "mode": "enforce",
                    "compiler_version": "artifact-media-enforce-v1",
                }
            ),
            custom_provider=provider,
            context_candidate_policy=policy,
        )
        model.provider_name = "openai"
        call_kwargs = {
            "context": context,
            "max_tokens": 128,
            "_aworld_artifact_vision_enabled": True,
            "_aworld_artifact_agent_id": "agent-1",
        }
        if asynchronous:
            await model.acompletion(legacy_messages, **call_kwargs)
        else:
            model.completion(legacy_messages, **call_kwargs)
        return context

    sync_context = await run_call(asynchronous=False)
    async_context = await run_call(asynchronous=True)

    for sent, context in zip((sync_calls[0], async_calls[0]), (sync_context, async_context)):
        serialized = json.dumps(sent)
        assert "CANDIDATE-STABLE" in serialized
        assert "LEGACY-STABLE" not in serialized
        assert "data:image/png;base64," in serialized
        record = context.get_llm_calls()[0]
        assert record["request_selection"] == "candidate"
        assert record["context_rollout"]["candidate_applied"] is True
        assert record["context_rollout"]["provider_lowering_ready"] is True
        assert record["context_rollout"]["artifact_observation"][
            "provider_cache_eligible"
        ] is True
        assert sent["messages"][0] == {
            "role": "system",
            "content": "CANDIDATE-STABLE",
        }
        provider_payload = record["provider_request"]["payload"]
        provider_serialized = json.dumps(provider_payload)
        assert "CANDIDATE-STABLE" in provider_serialized
        assert "LEGACY-STABLE" not in provider_serialized
        assert "data:image" not in provider_serialized
        assert provider_payload["messages"][0] == sent["messages"][0]
        assert record["provider_request"]["fidelity"] == (
            "provider_prepared_media_redacted"
        )


@pytest.mark.asyncio
async def test_observe_media_preserves_legacy_observed_attribution() -> None:
    calls: list[dict[str, Any]] = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return object()

    provider = object.__new__(OpenAIProvider)
    provider.model_name = "gpt-test"
    provider.kwargs = {}
    provider.base_url = None
    provider.provider = None
    provider.async_provider = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    provider.is_http_provider = False
    provider.stream_tool_buffer = []
    provider.postprocess_response = MethodType(
        lambda self, response: _CapturingProvider._response(), provider
    )
    model = LLMModel(
        conf=ModelConfig(context_compiler={"mode": "observe"}),
        custom_provider=provider,
    )
    model.provider_name = "openai"
    context = Context(task_id="artifact-observe-media")
    context.trace_id = ""

    await model.acompletion(
        _causal_messages(context),
        context=context,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )

    assert "data:image/png;base64," in json.dumps(calls[0])
    record = context.get_llm_calls()[0]
    evidence = record["context_rollout"]["provider_attribution"]
    assert evidence["status"] == "available"
    assert evidence["subject"] == "legacy_observed"
    assert evidence["attribution"]["subject"] == "legacy_observed"
    assert record["provider_request"]["fidelity"] == (
        "provider_prepared_media_redacted"
    )


def test_shared_anthropic_enforce_applies_media_only_to_candidate_suffix() -> None:
    calls: list[dict[str, Any]] = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return object()

    context = Context(task_id="artifact-anthropic-enforce")
    context.trace_id = ""
    legacy_messages = [
        {"role": "system", "content": "LEGACY-STABLE"},
        *_causal_messages(context),
    ]
    candidate_messages = deepcopy(legacy_messages)
    candidate_messages[0]["content"] = "CANDIDATE-STABLE"
    provider = object.__new__(AnthropicProvider)
    provider.model_name = "claude-test"
    provider.kwargs = {}
    provider.provider = SimpleNamespace(messages=Messages())
    provider.async_provider = None
    provider.stream_tool_buffer = []
    provider.postprocess_response = MethodType(
        lambda self, response: _CapturingProvider._response(), provider
    )
    policy = CandidateCompilePolicy(
        compiler_version="artifact-anthropic-media-v1",
        candidate_payload={
            "messages": candidate_messages,
            "tools": None,
            "params": {
                "temperature": 0,
                "max_tokens": 128,
                "stop": None,
            },
        },
        enforce_ready=True,
    )
    model = LLMModel(
        conf=ModelConfig(
            context_compiler={
                "mode": "enforce",
                "compiler_version": "artifact-anthropic-media-v1",
            }
        ),
        custom_provider=provider,
        context_candidate_policy=policy,
    )
    model.provider_name = "anthropic"

    model.completion(
        legacy_messages,
        context=context,
        max_tokens=128,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )

    assert calls[0]["system"] == "CANDIDATE-STABLE"
    serialized = json.dumps(calls[0])
    assert "LEGACY-STABLE" not in serialized
    assert '"type": "image"' in serialized
    assert base64.b64encode(_PNG_1X1).decode("ascii") in serialized
    record = context.get_llm_calls()[0]
    assert record["request_selection"] == "candidate"
    assert record["context_rollout"]["candidate_applied"] is True
    snapshot = json.dumps(record["provider_request"]["payload"])
    assert "CANDIDATE-STABLE" in snapshot
    assert "LEGACY-STABLE" not in snapshot
    assert base64.b64encode(_PNG_1X1).decode("ascii") not in snapshot


def test_anthropic_media_preserves_universal_native_cache_prefix() -> None:
    calls: list[dict[str, Any]] = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return object()

    context = Context(task_id="artifact-anthropic-cache")
    context.trace_id = ""
    context.advance_context_lifecycle("checkpoint")
    messages = [
        {"role": "system", "content": "CACHE-STABLE"},
        *_causal_messages(context),
    ]
    provider = object.__new__(AnthropicProvider)
    provider.model_name = "claude-test"
    provider.kwargs = {}
    provider.provider = SimpleNamespace(messages=Messages())
    provider.async_provider = None
    provider.stream_tool_buffer = []
    provider.postprocess_response = MethodType(
        lambda self, response: _CapturingProvider._response(), provider
    )
    model = LLMModel(
        conf=ModelConfig(
            context_cache={"allow_provider_native_cache": True},
            context_compiler={"mode": "enforce", "universal_final": True},
        ),
        custom_provider=provider,
    )
    model.provider_name = "anthropic"

    model.completion(
        messages,
        context=context,
        max_tokens=128,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )

    assert calls[0]["system"] == [
        {
            "type": "text",
            "text": "CACHE-STABLE",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert base64.b64encode(_PNG_1X1).decode("ascii") in json.dumps(calls[0])
    record = context.get_llm_calls()[0]
    cache_plan = record["context_rollout"]["final_compile"]["cache_plan"]
    assert cache_plan["stable_message_count"] == 1
    lowering = record["context_rollout"]["provider_lowering"]
    assert lowering["cache_lowering_status"] == "applied"
    assert lowering["cache_lowering_strategy"] == "anthropic_cache_control"
    assert lowering["cache_plan_fingerprint"] == cache_plan["fingerprint"]
    assert (
        VerifiedContextEntrypointParityReceipt.from_llm_call_record(record)
        .receipt.provider_bound
        is True
    )


def test_openai_http_media_preserves_serialized_native_cache_prefix() -> None:
    sent: list[tuple[dict[str, Any], bytes | None]] = []

    class HTTP:
        def sync_call(self, data, *, serialized_body=None):
            sent.append((data, serialized_body))
            return object()

    context = Context(task_id="artifact-openai-http-cache")
    context.trace_id = ""
    context.advance_context_lifecycle("checkpoint")
    messages = [
        {"role": "system", "content": "CACHE-STABLE"},
        *_causal_messages(context),
    ]
    provider = object.__new__(OpenAIProvider)
    provider.model_name = "gpt-test"
    provider.kwargs = {}
    provider.base_url = None
    provider.provider = SimpleNamespace()
    provider.async_provider = None
    provider.is_http_provider = True
    provider.http_provider = HTTP()
    provider.stream_tool_buffer = []
    provider.postprocess_response = MethodType(
        lambda self, response: _CapturingProvider._response(), provider
    )
    model = LLMModel(
        conf=ModelConfig(
            context_cache={"allow_provider_native_cache": True},
            context_compiler={"mode": "enforce", "universal_final": True},
        ),
        custom_provider=provider,
    )
    model.provider_name = "openai"

    model.completion(
        messages,
        context=context,
        max_tokens=128,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )

    payload, serialized_body = sent[0]
    assert payload["messages"][0] == {
        "role": "system",
        "content": "CACHE-STABLE",
    }
    assert "data:image/png;base64," in json.dumps(payload)
    assert serialized_body is not None
    record = context.get_llm_calls()[0]
    snapshot = json.dumps(record["provider_request"]["payload"])
    assert "CACHE-STABLE" in snapshot
    assert "data:image" not in snapshot
    lowering = record["context_rollout"]["provider_lowering"]
    assert lowering["cache_lowering_status"] == "preserved"
    assert lowering["cache_lowering_strategy"] == "exact_prefix_no_hint"
    assert (
        VerifiedContextEntrypointParityReceipt.from_llm_call_record(record)
        .receipt.provider_bound
        is True
    )


@pytest.mark.asyncio
async def test_undeclared_provider_media_capability_stays_compact_and_tool_free() -> (
    None
):
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    provider = _UnsupportedMediaProvider()
    model = LLMModel(custom_provider=provider)

    await model.acompletion(
        messages,
        context=context,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )

    serialized = json.dumps(provider.calls[0])
    assert ARTIFACT_RETAINED_MESSAGE in serialized
    assert "data:image" not in serialized
    assert '"type": "image"' not in serialized
    assert not any(
        key.startswith("_aworld_artifact") for key in provider.kwargs_calls[0]
    )


@pytest.mark.asyncio
async def test_partial_stream_failure_does_not_consume_image_delivery() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    provider = _PartialFailProvider()
    model = LLMModel(custom_provider=provider)

    with pytest.raises(RuntimeError, match="partial stream failure"):
        _ = [
            chunk
            async for chunk in model.astream_completion(
                messages,
                context=context,
                _aworld_artifact_vision_enabled=True,
                _aworld_artifact_agent_id="agent-1",
            )
        ]
    assert "data:image/png;base64," in json.dumps(provider.calls[0])

    completed = [
        chunk
        async for chunk in model.astream_completion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
    ]
    assert completed
    assert "data:image/png;base64," in json.dumps(provider.calls[1])

    stable = [
        chunk
        async for chunk in model.astream_completion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
    ]
    assert stable
    assert "data:image" not in json.dumps(provider.calls[2])


def test_partial_sync_stream_failure_does_not_consume_image_delivery() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    provider = _SyncPartialFailProvider()
    model = LLMModel(custom_provider=provider)

    with pytest.raises(RuntimeError, match="partial sync stream failure"):
        list(
            model.stream_completion(
                messages,
                context=context,
                _aworld_artifact_vision_enabled=True,
                _aworld_artifact_agent_id="agent-1",
            )
        )
    assert "data:image/png;base64," in json.dumps(provider.calls[0])

    assert list(
        model.stream_completion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
    )
    assert "data:image/png;base64," in json.dumps(provider.calls[1])


@pytest.mark.asyncio
async def test_empty_stream_consumes_one_shot_image_delivery() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    provider = _EmptyThenSuccessProvider()
    model = LLMModel(custom_provider=provider)

    assert [
        chunk
        async for chunk in model.astream_completion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
    ] == []
    assert "data:image/png;base64," in json.dumps(provider.calls[0])

    assert [
        chunk
        async for chunk in model.astream_completion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
    ]
    assert "data:image" not in json.dumps(provider.calls[1])


def _artifact_tool_schema(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "test tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _artifact_agent(provider: LLMProviderBase, *, attempts: int = 2) -> Agent:
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="capability-contract-test",
            llm_api_key="test-key",
            use_vision=True,
            artifact_observation_media_capability="supported",
        ),
        generation_budget_policy=GenerationBudgetPolicy(total_timeout_seconds=5),
        llm_max_attempts=attempts,
        llm_retry_delay=0,
    )
    agent._llm = LLMModel(custom_provider=provider)
    return agent


def _artifact_message(context: Context) -> Message:
    context.set_task(
        Task(
            id=context.task_id,
            session_id=context.session_id,
            user_id="user-1",
            input="inspect the artifact",
        )
    )
    return Message(
        category=Constants.AGENT,
        sender="user",
        receiver="Aworld",
        headers={"context": context},
    )


@pytest.mark.asyncio
async def test_media_empty_response_gets_one_text_only_recovery() -> None:
    context = Context(
        task_id="artifact-media-empty-recovery",
        session=Session(session_id="artifact-media-empty-recovery-session"),
    )
    provider = _EmptyThenSuccessProvider()
    agent = _artifact_agent(provider)
    message = _artifact_message(context)
    tools = [
        _artifact_tool_schema("observe_artifact"),
        _artifact_tool_schema("workspace__write"),
    ]

    result = await agent.invoke_model(
        _causal_messages(context),
        message=message,
        prepared_tools=tools,
        stream=True,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id=agent.id(),
    )

    assert result.content == "inspected"
    assert len(provider.calls) == 2
    assert "data:image/png;base64," in json.dumps(provider.calls[0])
    assert "data:image" not in json.dumps(provider.calls[1])
    assert [
        tool["function"]["name"]
        for tool in provider.kwargs_calls[1]["tools"]
    ] == ["workspace__write"]
    assert provider.kwargs_calls[1]["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_media_empty_recovery_exhaustion_is_typed_and_nonrecoverable() -> None:
    context = Context(
        task_id="artifact-media-empty-exhausted",
        session=Session(session_id="artifact-media-empty-exhausted-session"),
    )
    provider = _AlwaysEmptyStreamProvider()
    agent = _artifact_agent(provider)
    message = _artifact_message(context)

    result = await agent.invoke_model(
        _causal_messages(context),
        message=message,
        prepared_tools=[
            _artifact_tool_schema("observe_artifact"),
            _artifact_tool_schema("workspace__write"),
        ],
        stream=True,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id=agent.id(),
    )

    assert len(provider.calls) == 2
    assert "data:image/png;base64," in json.dumps(provider.calls[0])
    assert "data:image" not in json.dumps(provider.calls[1])
    assert result.message["aworld_incomplete_reason"] == (
        "model_response_artifact_media_recovery_exhausted"
    )
    assert result.message["aworld_recoverable"] is False
    diagnostic = context.context_info[f"artifact_media_recovery:{agent.id()}"]
    assert diagnostic == {
        "schema_version": "aworld.artifact-media-recovery/v1",
        "status": "recovery_exhausted",
        "reason": "empty_model_response_after_artifact_media",
        "recovery_failure_reason": "empty_model_response",
    }


@pytest.mark.asyncio
async def test_undeclared_media_capability_omits_and_rejects_observe_artifact() -> None:
    context = Context(task_id="artifact-media-unsupported")
    agent = _artifact_agent(_UnsupportedMediaProvider(), attempts=1)
    agent.tools = [
        _artifact_tool_schema("observe_artifact"),
        _artifact_tool_schema("workspace__write"),
    ]

    selected = await agent._filter_tools(context)

    assert [tool["function"]["name"] for tool in selected] == [
        "workspace__write"
    ]
    diagnostic = context.context_info[f"artifact_media_capability:{agent.id()}"]
    assert diagnostic == {
        "schema_version": "aworld.artifact-media-capability/v1",
        "available": False,
        "reason": "provider_media_capability_undeclared",
    }

    response = ModelResponse(
        id="unsupported-media-call",
        model="capability-contract-test",
        tool_calls=[
            ToolCall(
                id="call-observe",
                function=Function(
                    name="observe_artifact",
                    arguments='{"path":"chart.png"}',
                ),
            )
        ],
    )
    with pytest.raises(ToolCallBatchParseError) as exc_info:
        await LlmOutputParser().parse(
            response,
            agent_id=agent.id(),
            agent=agent,
        )
    assert exc_info.value.issues[0].code is (
        ToolCallParseIssueCode.MEDIA_CAPABILITY_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_stream_without_terminal_finish_does_not_consume_image_delivery() -> (
    None
):
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    provider = _UnfinishedThenSuccessProvider()
    model = LLMModel(custom_provider=provider)

    assert [
        chunk
        async for chunk in model.astream_completion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
    ]
    assert "data:image/png;base64," in json.dumps(provider.calls[0])

    assert [
        chunk
        async for chunk in model.astream_completion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
    ]
    assert "data:image/png;base64," in json.dumps(provider.calls[1])


@pytest.mark.asyncio
async def test_response_parser_failure_does_not_consume_image_delivery() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    provider = _CapturingProvider()
    model = LLMModel(custom_provider=provider)
    model.llm_response_parser = _FailOnceParser()

    with pytest.raises(RuntimeError, match="parser rejected response"):
        await model.acompletion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
    assert "data:image/png;base64," in json.dumps(provider.calls[0])

    await model.acompletion(
        messages,
        context=context,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )
    assert "data:image/png;base64," in json.dumps(provider.calls[1])


@pytest.mark.asyncio
async def test_none_provider_response_does_not_consume_image_delivery() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    provider = _NoneThenSuccessProvider()
    model = LLMModel(custom_provider=provider)

    assert (
        await model.acompletion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
        is None
    )
    assert "data:image/png;base64," in json.dumps(provider.calls[0])

    assert await model.acompletion(
        messages,
        context=context,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )
    assert "data:image/png;base64," in json.dumps(provider.calls[1])


@pytest.mark.asyncio
async def test_none_parser_response_does_not_consume_image_delivery() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    provider = _CapturingProvider()
    model = LLMModel(custom_provider=provider)
    model.llm_response_parser = _NoneThenSuccessParser()

    assert (
        await model.acompletion(
            messages,
            context=context,
            _aworld_artifact_vision_enabled=True,
            _aworld_artifact_agent_id="agent-1",
        )
        is None
    )
    assert "data:image/png;base64," in json.dumps(provider.calls[0])

    assert await model.acompletion(
        messages,
        context=context,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )
    assert "data:image/png;base64," in json.dumps(provider.calls[1])


@pytest.mark.asyncio
async def test_media_call_preserves_prompt_cache_inputs_and_never_leaks_private_kwargs() -> (
    None
):
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    provider = _CapturingProvider()
    model = LLMModel(custom_provider=provider)

    await model.acompletion(
        messages,
        context=context,
        prompt_assembly_plan="stable-plan",
        provider_native_prompt_cache=True,
        _aworld_artifact_vision_enabled=True,
        _aworld_artifact_agent_id="agent-1",
    )

    sent_kwargs = provider.kwargs_calls[0]
    assert sent_kwargs["prompt_assembly_plan"] == "stable-plan"
    assert sent_kwargs["provider_native_prompt_cache"] is True
    assert not any(key.startswith("_aworld_artifact") for key in sent_kwargs)


def test_projection_retry_and_non_vision_fallback_are_compact() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)

    first, receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=True,
        media_projection=OPENAI_MEDIA_PROJECTION.projection,
    )
    retry, retry_receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=True,
        media_projection=OPENAI_MEDIA_PROJECTION.projection,
    )
    assert receipt["hydrated_count"] == retry_receipt["hydrated_count"] == 1
    assert "data:image" in json.dumps(first)
    assert "data:image" in json.dumps(retry)
    commit_artifact_projection(context, agent_id="agent-1", receipt=receipt)
    retained, retained_receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=True,
        media_projection=OPENAI_MEDIA_PROJECTION.projection,
    )
    assert retained_receipt["hydrated_count"] == 0
    assert "data:image" not in json.dumps(retained)

    context.advance_context_lifecycle("checkpoint")
    degraded, degraded_receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=False,
        media_projection=OPENAI_MEDIA_PROJECTION.projection,
    )
    assert degraded_receipt["hydrated_count"] == 0
    assert degraded_receipt["degraded_count"] == 0
    assert ARTIFACT_RETAINED_MESSAGE in json.dumps(degraded)
    assert "aworld-artifact://" not in json.dumps(degraded)


def test_provider_projection_prioritizes_newest_images_in_one_tool_batch() -> None:
    context = Context(task_id="artifact-task")
    groups = [_causal_messages(context, call_id=f"call-{index}") for index in range(5)]
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [group[0]["tool_calls"][0] for group in groups],
        },
        *(group[1] for group in groups),
        groups[-1][2],
    ]

    hydrated, receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=True,
        media_projection=OPENAI_MEDIA_PROJECTION.projection,
    )

    assert receipt["hydrated_count"] == 4
    assert receipt["hydrated_bytes"] == 4 * len(_PNG_1X1)
    assert receipt["degraded_count"] == 1
    assert json.dumps(hydrated).count("data:image/png;base64,") == 4
    assert "degraded 1 overflow" in json.dumps(hydrated[-1])
    assert len(hydrated[-1]["content"]) == 5
    expected_newest = {
        artifact_module._delivery_key(
            {
                "observation_id": json.loads(group[1]["content"])["observation_id"],
                "call_id_hash": json.loads(group[1]["content"])["call_id_hash"],
            }
        )
        for group in groups[1:]
    }
    assert set(receipt["hydrated_attempt_keys"]) == expected_newest


@pytest.mark.parametrize("shape", ["orphan_result", "wrong_tool", "missing_result"])
def test_provider_projection_requires_complete_framework_causal_chain(
    shape: str,
) -> None:
    context = Context(task_id="artifact-task")
    valid = _causal_messages(context, call_id="call-causal")
    assistant, tool_result, marker = valid
    if shape == "orphan_result":
        messages = [tool_result, marker]
    elif shape == "wrong_tool":
        wrong = json.loads(json.dumps(assistant))
        wrong["tool_calls"][0]["function"]["name"] = "read_file"
        messages = [wrong, tool_result, marker]
    else:
        incomplete = json.loads(json.dumps(assistant))
        incomplete["tool_calls"].append(
            {
                "id": "missing-call",
                "type": "function",
                "function": {"name": "run_code", "arguments": "{}"},
            }
        )
        messages = [incomplete, tool_result, marker]

    projected, receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=True,
        media_projection=OPENAI_MEDIA_PROJECTION.projection,
    )

    assert receipt["hydrated_count"] == 0
    assert "data:image" not in json.dumps(projected)


def test_provider_projection_recovers_receipt_from_sanitized_tool_text() -> None:
    context = Context(task_id="artifact-task")
    messages = sanitize_openai_messages(_causal_messages(context))

    projected, receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=True,
        media_projection=OPENAI_MEDIA_PROJECTION.projection,
    )

    assert receipt["hydrated_count"] == 1
    assert "data:image/png;base64," in json.dumps(projected)


def test_budget_recovery_does_not_pin_an_expired_artifact_sidecar() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)

    assert artifact_module.artifact_marker_recovery_states(
        messages,
        context=context,
        agent_id="agent-1",
    ) == {2: "undelivered"}

    clear_artifact_observation_state()

    assert artifact_module.artifact_marker_recovery_states(
        messages,
        context=context,
        agent_id="agent-1",
    ) == {2: "unavailable"}


def test_budget_recovery_does_not_consume_a_spoofed_artifact_marker() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)
    messages[1]["content"] = "not a valid scoped artifact receipt"

    assert messages[2]["content"] == ARTIFACT_RETAINED_MESSAGE
    assert artifact_module.artifact_marker_recovery_states(
        messages,
        context=context,
        agent_id="agent-1",
    ) == {}


def test_budget_recovery_preserves_partially_available_media_group() -> None:
    context = Context(task_id="artifact-task")
    first = _causal_messages(
        context,
        call_id="call-expired",
        path_key="sha256:path-expired",
    )
    second = _causal_messages(
        context,
        call_id="call-available",
        path_key="sha256:path-available",
    )
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                first[0]["tool_calls"][0],
                second[0]["tool_calls"][0],
            ],
        },
        first[1],
        second[1],
        {"role": "user", "content": ARTIFACT_RETAINED_MESSAGE},
    ]

    def expire_sidecar(tool_message: dict[str, Any]) -> None:
        receipt = artifact_module.artifact_receipt_from_tool_content(
            tool_message["content"]
        )
        identity = (receipt["task_scope_hash"], receipt["observation_id"])
        with artifact_module._state_lock:
            token = artifact_module._sidecar_identity.pop(identity)
            entry = artifact_module._sidecars.pop(token)
            artifact_module._sidecar_bytes -= len(entry.data)

    expire_sidecar(first[1])
    assert artifact_module.artifact_marker_recovery_states(
        messages,
        context=context,
        agent_id="agent-1",
    ) == {3: "undelivered"}

    expire_sidecar(second[1])
    assert artifact_module.artifact_marker_recovery_states(
        messages,
        context=context,
        agent_id="agent-1",
    ) == {3: "unavailable"}


@pytest.mark.asyncio
async def test_memory_stores_stable_receipt_but_not_reference_or_image_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = Context(task_id="artifact-task")
    context.set_task(
        Task(
            id="artifact-task",
            session_id=context.session_id,
            user_id="user-1",
            input="inspect",
        )
    )
    call_id = "call-1"
    observed = observe_artifact_bytes(
        _PNG_1X1,
        suffix=".png",
        path_key="sha256:path",
        file_epoch=_FILE_EPOCH,
        framework_scope={
            "task_id": context.task_id,
            "session_id": context.session_id,
            "task_epoch": context.task_epoch,
            "tool_call_id": call_id,
        },
    )
    result = lower_mcp_call_result(
        CallToolResult(content=artifact_mcp_content(observed)),
        server_name="terminal",
        tool_name="observe_artifact",
        trusted_artifact_observation=True,
    )
    result.tool_call_id = call_id
    memory = _Memory()
    monkeypatch.setattr(
        "aworld.runners.handler.memory.MemoryFactory",
        type("MemoryFactory", (), {"instance": staticmethod(lambda: memory)}),
    )
    handler = DefaultMemoryHandler(SimpleNamespace(task=SimpleNamespace(hooks={})))

    await handler._do_add_tool_result_to_memory(_Agent(), call_id, result, context)

    serialized = json.dumps(memory.items[0].model_dump(mode="json"), ensure_ascii=False)
    assert "aworld.artifact-observation/v1" in serialized
    assert "aworld-artifact://observation/" not in serialized
    assert base64.b64encode(_PNG_1X1).decode("ascii") not in serialized
    assert "data:image" not in serialized

    sentinel = "data:image/png;base64,PRIVATE-MEMORY-SENTINEL"
    invalid_blocks = artifact_mcp_content(observed)
    invalid_blocks.append(TextContent(type="text", text=sentinel))
    invalid = lower_mcp_call_result(
        CallToolResult(content=invalid_blocks),
        server_name="terminal",
        tool_name="observe_artifact",
        trusted_artifact_observation=True,
    )
    invalid.tool_call_id = "call-2"
    await handler._do_add_tool_result_to_memory(_Agent(), "call-2", invalid, context)
    invalid_serialized = json.dumps(
        memory.items[-1].model_dump(mode="json"), ensure_ascii=False
    )
    assert sentinel not in invalid_serialized
    assert base64.b64encode(_PNG_1X1).decode("ascii") not in invalid_serialized


def test_openai_prepared_snapshot_uses_reference_while_wire_uses_image() -> None:
    provider = object.__new__(OpenAIProvider)
    provider.model_name = "gpt-test"
    provider.kwargs = {}
    provider.is_http_provider = False
    captured = {}

    def capture(self, *, snapshot, **kwargs):
        captured["snapshot"] = snapshot

    provider.commit_provider_prepared_attempt = MethodType(capture, provider)
    provider.context_candidate_lowering_capability = MethodType(
        OpenAIProvider.context_candidate_lowering_capability, provider
    )
    data_url = "data:image/png;base64,aGVsbG8="
    redacted = [
        {"role": "system", "content": "stable policy"},
        {
            "role": "user",
            "content": ARTIFACT_RETAINED_MESSAGE,
        },
    ]
    plan = DefaultPromptAssemblyProvider().build_plan(
        messages=redacted,
        tools=[],
        metadata={},
    )
    stage_provider_media_audit(
        provider,
        request_id="artifact-request",
        messages=redacted,
    )
    prepared = provider._prepare_chat_completion_request(
        messages=[
            {"role": "system", "content": "stable policy"},
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": data_url}}],
            },
        ],
        temperature=0,
        max_tokens=128,
        stop=None,
        kwargs={
            "llm_request_id": "artifact-request",
            "context": SimpleNamespace(),
            "prompt_assembly_plan": plan,
            "provider_native_prompt_cache": True,
        },
        stream=False,
    )

    assert data_url in json.dumps(prepared.params)
    assert prepared.params["prompt_cache_key"] == plan.stable_hash
    snapshot = json.dumps(captured["snapshot"].thaw())
    assert ARTIFACT_RETAINED_MESSAGE in snapshot
    assert data_url not in snapshot
    assert captured["snapshot"].fidelity is (
        ProviderRequestFidelity.PROVIDER_PREPARED_MEDIA_REDACTED
    )


def test_shared_provider_lowering_snapshots_reference_not_image_bytes() -> None:
    captured = {}
    capability = ProviderLoweringCapability(
        provider_name="demo",
        adapter_identity="demo.adapter",
        adapter_version="v1",
        request_projection="demo.v1",
    )

    class Provider:
        @staticmethod
        def context_candidate_lowering_capability():
            return capability

        @staticmethod
        def commit_provider_prepared_attempt(*, snapshot, **kwargs):
            captured["snapshot"] = snapshot

    def lower(selected, request_kwargs, stream, cache_plan):
        return ProviderWireProjection(
            payload={"messages": selected["messages"]},
            message_occurrences=(),
            tool_occurrences=None,
        )

    data_url = "data:image/png;base64,aGVsbG8="
    redacted = [{"role": "user", "content": "aworld-artifact://observation/ref"}]
    provider = Provider()
    stage_provider_media_audit(
        provider,
        request_id="request-1",
        messages=redacted,
    )
    prepared = prepare_provider_context_request(
        provider=provider,
        messages=[{"role": "user", "content": data_url}],
        temperature=0,
        max_tokens=32,
        stop=None,
        kwargs={
            "llm_request_id": "request-1",
        },
        stream=False,
        lower=lower,
    )

    assert data_url in json.dumps(prepared.payload)
    snapshot = json.dumps(captured["snapshot"].thaw())
    assert "aworld-artifact://observation/ref" in snapshot
    assert data_url not in snapshot
    assert captured["snapshot"].fidelity is (
        ProviderRequestFidelity.PROVIDER_PREPARED_MEDIA_REDACTED
    )


def test_anthropic_prepared_wire_uses_native_image_source_and_redacted_snapshot() -> (
    None
):
    provider = object.__new__(AnthropicProvider)
    provider.model_name = "claude-test"
    provider.kwargs = {}
    provider.base_url = None
    captured = {}

    def capture(self, *, snapshot, **kwargs):
        captured["snapshot"] = snapshot

    provider.commit_provider_prepared_attempt = MethodType(capture, provider)
    encoded = base64.b64encode(_PNG_1X1).decode("ascii")
    wire_messages = [
        {"role": "system", "content": "stable policy"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": ARTIFACT_RETAINED_MESSAGE},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": encoded,
                    },
                },
            ],
        },
    ]
    audit_messages = [
        {"role": "system", "content": "stable policy"},
        {"role": "user", "content": ARTIFACT_RETAINED_MESSAGE},
    ]
    plan = DefaultPromptAssemblyProvider().build_plan(
        messages=audit_messages,
        tools=[],
        metadata={},
    )
    stage_provider_media_audit(
        provider,
        request_id="anthropic-artifact",
        messages=audit_messages,
    )

    prepared = provider._prepare_context_request(
        wire_messages,
        temperature=0,
        max_tokens=128,
        stop=None,
        kwargs={
            "llm_request_id": "anthropic-artifact",
            "context": SimpleNamespace(),
            "prompt_assembly_plan": plan,
            "provider_native_prompt_cache": True,
        },
        stream=False,
    )

    assert prepared.payload["messages"][0]["content"][1]["source"]["data"] == encoded
    assert prepared.payload["cache_control"] == {"type": "ephemeral"}
    snapshot = json.dumps(captured["snapshot"].thaw())
    assert ARTIFACT_RETAINED_MESSAGE in snapshot
    assert encoded not in snapshot
    assert captured["snapshot"].fidelity is (
        ProviderRequestFidelity.PROVIDER_PREPARED_MEDIA_REDACTED
    )


def test_agent_vision_gate_honors_config_and_provider_declaration() -> None:
    disabled = Agent(
        name="no-vision",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
            use_vision=False,
        ),
    )
    assert disabled._artifact_vision_enabled() is False

    route_unspecified = Agent(
        name="route-unspecified",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
            use_vision=True,
        ),
    )
    assert route_unspecified._artifact_vision_enabled() is False

    enabled = Agent(
        name="vision",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
            use_vision=True,
            artifact_observation_media_capability="supported",
        ),
    )
    assert enabled._artifact_vision_enabled() is True
    enabled.llm.provider.provider_media_projection_capability = lambda: None
    assert enabled._artifact_vision_enabled() is False


def test_http_log_summary_redacts_openai_and_anthropic_image_payloads() -> None:
    handler = LLMHTTPHandler(
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model_name="test-model",
    )
    sentinel = "PRIVATE-WIRE-SENTINEL"
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{sentinel}"},
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": sentinel,
                        },
                    },
                ],
            }
        ]
    }

    summary = handler._summarize_request_data_for_log(payload)
    serialized = json.dumps(summary)
    assert sentinel not in serialized
    assert "data:image" not in serialized
    assert serialized.count("payload_sha256") == 2


@pytest.mark.asyncio
async def test_openai_trace_redacts_wire_image_payload(monkeypatch) -> None:
    monkeypatch.setenv("SHOULD_TRACE_PROMPTS", "true")
    sentinel = "PRIVATE-TRACE-SENTINEL"

    class Span:
        attributes = None

        @staticmethod
        def is_recording():
            return True

        def set_attributes(self, value):
            self.attributes = value

    span = Span()
    await handle_openai_request(
        span,
        {
            "model": "test-model",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{sentinel}"},
                        }
                    ],
                }
            ],
        },
        SimpleNamespace(_client=object()),
    )

    serialized = json.dumps(span.attributes, default=str)
    assert sentinel not in serialized
    assert "data:image" not in serialized
    assert "payload_sha256" in serialized


def test_openai_stream_token_estimate_counts_only_text_blocks(monkeypatch) -> None:
    counted: list[str] = []

    def count_text(value: str, model_name: str) -> int:
        counted.append(value)
        return len(value)

    monkeypatch.setattr(
        "aworld.trace.instrumentation.openai.inout_parse.get_token_count_from_string",
        count_text,
    )
    sentinel = "PRIVATE-BASE64-" + ("x" * 4096)

    prompt_tokens, completion_tokens = record_stream_token_usage(
        {
            "model": "gpt-test",
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "output_text", "text": "answer"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{sentinel}"
                                },
                            },
                        ]
                    }
                }
            ],
        },
        {
            "model": "gpt-test",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "question"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{sentinel}"
                            },
                        },
                    ],
                }
            ],
        },
    )

    assert (prompt_tokens, completion_tokens) == (len("question"), len("answer"))
    assert counted == ["question", "answer"]
    assert sentinel not in repr(counted)

    complete = {"choices": []}
    record_stream_response_chunk(
        {
            "model": "gpt-test",
            "id": "chunk-2",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": [
                            {"type": "text", "text": "visible"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{sentinel}"
                                },
                            },
                        ]
                    },
                }
            ],
        },
        complete,
    )
    assert complete["choices"][0]["message"]["content"] == "visible"
    assert sentinel not in repr(complete)
