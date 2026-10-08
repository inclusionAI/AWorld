from __future__ import annotations

import base64
import json
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

import aworld.sandbox.artifact_observation as artifact_module
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    ProviderLoweringCapability,
    ProviderRequestFidelity,
)
from aworld.core.context.amni.prompt.assembly.provider import (
    DefaultPromptAssemblyProvider,
)
from aworld.core.llm_provider import LLMProviderBase
from aworld.core.task import Task
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, AgentMemoryConfig
from aworld.mcp_client.utils import lower_mcp_call_result
from aworld.models.llm import LLMModel
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import ModelResponse
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
from aworld.trace.instrumentation.openai.inout_parse import handle_openai_request
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


def _causal_messages(context: Context, call_id: str = "call-1"):
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
    assert rollout["candidate_applied"] is False
    assert rollout["candidate_status"] == "late_bound_artifact_transport"
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
async def test_empty_stream_does_not_consume_image_delivery() -> None:
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

    enabled = Agent(
        name="vision",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
            use_vision=True,
        ),
    )
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
