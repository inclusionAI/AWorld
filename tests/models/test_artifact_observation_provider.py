from __future__ import annotations

import base64
import json
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from mcp.types import CallToolResult

from aworld.core.context.base import Context
from aworld.core.context.compiler import ProviderLoweringCapability
from aworld.core.llm_provider import LLMProviderBase
from aworld.core.task import Task
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, AgentMemoryConfig
from aworld.mcp_client.utils import lower_mcp_call_result
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse
from aworld.models.openai_provider import OpenAIProvider
from aworld.models.provider_context_request import (
    ProviderWireProjection,
    prepare_provider_context_request,
)
from aworld.runners.handler.memory import DefaultMemoryHandler
from aworld.sandbox.artifact_observation import (
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
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "/x8AAusB9Y9Z4E8AAAAASUVORK5CYII="
)
_FILE_EPOCH = "sha256:" + ("0" * 64)


class _CapturingProvider(LLMProviderBase):
    def __init__(self) -> None:
        super().__init__(model_name="vision-test")
        self.calls: list[list[dict[str, Any]]] = []

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
        return self._response()

    def completion(self, messages, **kwargs):
        self.calls.append(messages)
        return self._response()

    async def astream_completion(self, messages, **kwargs):
        self.calls.append(messages)
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
    assert rollout["artifact_observation"]["provider_cache_eligible"] is False

    # A new Context checkpoint permits one deliberate rehydration for stream
    # mode; ordinary same-checkpoint turns retain only the compact reference.
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
    assert provider.calls[-1][-1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


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
    assert "not configured for vision" in serialized


def test_projection_retry_and_non_vision_fallback_are_compact() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context)

    first, receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=True,
    )
    retry, retry_receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=True,
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
    )
    assert retained_receipt["hydrated_count"] == 0
    assert "data:image" not in json.dumps(retained)

    context.advance_context_lifecycle("checkpoint")
    degraded, degraded_receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=False,
    )
    assert degraded_receipt["hydrated_count"] == 0
    assert degraded_receipt["degraded_count"] == 1
    assert "not configured for vision" in json.dumps(degraded)
    assert "aworld-artifact://" not in json.dumps(degraded)


def test_provider_projection_bounds_images_across_multiple_tool_groups() -> None:
    context = Context(task_id="artifact-task")
    messages = _causal_messages(context) * 5

    hydrated, receipt = hydrate_artifact_messages(
        messages,
        context=context,
        agent_id="agent-1",
        vision_enabled=True,
    )

    assert receipt["hydrated_count"] == 4
    assert receipt["hydrated_bytes"] == 4 * len(_PNG_1X1)
    assert receipt["degraded_count"] == 1
    assert json.dumps(hydrated).count("data:image/png;base64,") == 4


@pytest.mark.asyncio
async def test_memory_stores_receipt_and_reference_but_not_image_bytes(
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
    assert "aworld-artifact://observation/" in serialized
    assert base64.b64encode(_PNG_1X1).decode("ascii") not in serialized
    assert "data:image" not in serialized


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
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "aworld-artifact://observation/ref-1"},
                }
            ],
        }
    ]
    prepared = provider._prepare_chat_completion_request(
        messages=[
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": data_url}}],
            }
        ],
        temperature=0,
        max_tokens=128,
        stop=None,
        kwargs={
            "llm_request_id": "artifact-request",
            "context": SimpleNamespace(),
            "_aworld_artifact_redacted_messages": redacted,
        },
        stream=False,
    )

    assert data_url in json.dumps(prepared.params)
    snapshot = json.dumps(captured["snapshot"].thaw())
    assert "aworld-artifact://observation/ref-1" in snapshot
    assert data_url not in snapshot


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
    prepared = prepare_provider_context_request(
        provider=Provider(),
        messages=[{"role": "user", "content": data_url}],
        temperature=0,
        max_tokens=32,
        stop=None,
        kwargs={
            "llm_request_id": "request-1",
            "_aworld_artifact_redacted_messages": redacted,
        },
        stream=False,
        lower=lower,
    )

    assert data_url in json.dumps(prepared.payload)
    snapshot = json.dumps(captured["snapshot"].thaw())
    assert "aworld-artifact://observation/ref" in snapshot
    assert data_url not in snapshot


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
    enabled.llm.provider.supports_vision = False
    assert enabled._artifact_vision_enabled() is False
