from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from aworld.config import ConfigDict, ModelConfig
from aworld.config.conf import ContextCompilerRuntimeConfig
from aworld.core.common import ActionResult
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    CandidateCompileInput,
    CandidateCompilePolicy,
    CandidateRequestNotEnforceable,
    ContextCompilerMode,
    ContextObservationSidecar,
    LifecycleAction,
    ProviderLoweringCapability,
    adapt_final_messages,
    canonical_json_hash,
    compile_context_candidate,
    compact_message_history,
    estimate_canonical_json_tokens,
)
from aworld.core.context.tool_output_runtime import (
    enforce_tool_output_boundary,
    prepare_tool_output_plans,
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


def _long_tool_exchange():
    return [
        {"role": "system", "content": "Keep tool arguments intact."},
        {"role": "user", "content": "Write the complete report."},
        {
            "role": "assistant",
            "content": "Writing the report.",
            "tool_calls": [{
                "id": "call-large-report", "type": "function",
                "function": {
                    "name": "write_report",
                    "arguments": json.dumps({"content": "report data\n" * 5000}),
                },
            }],
        },
        {"role": "tool", "tool_call_id": "call-large-report", "content": "written"},
    ]


@pytest.mark.parametrize("limit", [0, -1])
def test_config_rejects_nonpositive_explicit_item_limit(limit):
    with pytest.raises(ValueError):
        ContextCompilerRuntimeConfig(max_item_tokens=limit)


def _assert_exchange_preserved(sent, original):
    # Provider text-part normalization and the untrusted-data envelope are
    # expected; neither may change the call arguments or the Tool result body.
    assert sent[-2]["tool_calls"] == original[-2]["tool_calls"]
    assert sent[-2]["content"] == [{"type": "text", "text": original[-2]["content"]}]
    assert sent[-1]["tool_call_id"] == original[-1]["tool_call_id"]
    text = sent[-1]["content"][0]["text"]
    assert text.startswith("<aworld-untrusted-data ")
    assert text.endswith("</aworld-untrusted-data>")
    body = text.split("\n", 1)[1].rsplit("\n", 1)[0]
    assert json.loads(body) == [{"type": "text", "text": original[-1]["content"]}]


@pytest.mark.asyncio
async def test_default_budget_preserves_long_tool_exchange_in_every_call_shape():
    provider, calls = _azure_without_transport()
    model = LLMModel(conf=ModelConfig(), custom_provider=provider)
    model.provider_name = "azure_openai"
    messages = _long_tool_exchange()
    assert estimate_canonical_json_tokens(messages[2]).value > 10000
    assert ContextCompilerRuntimeConfig().max_item_tokens is None
    assert model.context_compiler_mode is ContextCompilerMode.ENFORCE

    contexts = [Context(task_id=f"long-exchange-{index}") for index in range(4)]
    for context in contexts:
        context.trace_id = ""
    model.completion(messages, context=contexts[0])
    await model.acompletion(messages, context=contexts[1])
    list(model.stream_completion(messages, context=contexts[2]))
    [chunk async for chunk in model.astream_completion(messages, context=contexts[3])]

    assert len(calls) == 4
    for sent in provider._test_sent_params:
        assert sent["messages"][:2] == messages[:2]
        _assert_exchange_preserved(sent["messages"], messages)
    assert all(
        context.get_llm_calls()[0]["context_rollout"]["candidate_applied"]
        for context in contexts
    )


@pytest.mark.parametrize("compiler_config,error_code", [
    ({"max_item_tokens": 10000}, "required_item_token_limit_exceeded"),
    ({"context_limit": 16000}, "required_context_budget_exceeded"),
])
def test_long_tool_exchange_still_rejects_explicit_cap_or_total_overflow(
    compiler_config, error_code,
):
    provider, calls = _azure_without_transport()
    model = LLMModel(
        conf=ModelConfig(context_compiler=compiler_config), custom_provider=provider,
    )
    model.provider_name = "azure_openai"
    context = Context(task_id="long-exchange-blocked")
    context.trace_id = ""
    with pytest.raises(CandidateRequestNotEnforceable, match=error_code):
        model.completion(_long_tool_exchange(), context=context)
    assert calls == []


@pytest.mark.asyncio
async def test_offload_and_history_compaction_feed_default_final_budget(tmp_path):
    from aworld.core.context.amni.processor.op.tool_result_process_op import ToolResultOffloadOp
    from aworld.memory.tool_result_compaction import compact_tool_result_for_memory

    provider, calls = _azure_without_transport()
    model = LLMModel(conf=ModelConfig(), custom_provider=provider)
    model.provider_name = "azure_openai"
    context = Context(task_id="offload-long-exchange", workspace_path=str(tmp_path))
    context.trace_id = ""
    context.configure_tool_output_boundary(model.enforced_tool_output_policy())
    raw = "0123456789abcdef" * 8192
    result = ActionResult(
        tool_call_id="call-large-report", tool_name="report", action_name="write",
        content=raw, metadata={},
    )
    action = SimpleNamespace(
        tool_call_id=result.tool_call_id, tool_name="report", action_name="write", params={},
    )
    enforce_tool_output_boundary(
        (SimpleNamespace(action_result=[result]),), (action,), context,
        prepare_tool_output_plans(context, (action,)),
    )
    record = context.get_tool_output_records()[0]
    assert record.artifact is not None
    assert record.inline_tokens <= 4096
    assert record.offloaded_tokens > 0
    assert context.read_tool_output_artifact(record.artifact.ref) == raw.encode()
    assert record.raw_checksum == "sha256:" + hashlib.sha256(raw.encode()).hexdigest()

    # AMNI and memory compaction must honor the existing reversible boundary,
    # even when their own thresholds would otherwise request another offload.
    amni_config = SimpleNamespace(
        tool_result_offload=True, tool_action_white_list=["report:write"],
        tool_result_length_threshold=1,
    )
    amni_context = SimpleNamespace(get_config=lambda: SimpleNamespace(
        get_agent_context_config=lambda agent_id: amni_config,
    ))
    assert not await ToolResultOffloadOp("tool_result_offload")._need_offload(
        result, amni_context, SimpleNamespace(agent_id="agent"),
    )
    memory = compact_tool_result_for_memory(
        result.content, force=True, result_metadata=result.metadata,
    )
    assert memory.applied is False
    assert memory.metadata["preserved_reversible_boundary"] is True

    messages = _long_tool_exchange()
    messages[-1]["content"] = json.dumps(result.content)
    old_history = [
        {"role": role, "content": f"old turn {index}"}
        for index in range(6) for role in ("user", "assistant")
    ]
    compacted, receipt = compact_message_history(
        messages[:2] + old_history + messages[2:], keep_recent=2,
    )
    assert receipt["removed_message_count"] == len(old_history)
    assert compacted[-2:] == messages[-2:]
    model.completion(compacted, context=context)
    assert len(calls) == 1
    _assert_exchange_preserved(provider._test_sent_params[0]["messages"], messages)
    assert context.read_tool_output_artifact(record.artifact.ref) == raw.encode()


def test_legacy_config_dict_without_new_cache_fields_remains_compatible():
    provider = CountingProvider()

    model = LLMModel(
        conf=ConfigDict({"max_model_len": 8192}),
        custom_provider=provider,
    )

    assert model._context_cache_config is None
    assert model._configured_max_tokens is None
    assert model._context_input_budget == 3328


def _recovery_context(tmp_path, monkeypatch):
    from aworld.core.context.amni import ApplicationContext
    from aworld.core.context.amni.state import ApplicationTaskContextState, TaskWorkingState, TaskInput
    from aworld.core.context.amni.worksapces import ApplicationWorkspace

    workspace = ApplicationWorkspace(
        workspace_id="recovery", storage_path=str(tmp_path), use_default_observer=False,
    )
    context = ApplicationContext(
        task_state=ApplicationTaskContextState(
            task_input=TaskInput(task_id="recovery-task", session_id="recovery-session", task_content="write report"),
            working_state=TaskWorkingState(),
        ),
        workspace=workspace, task_id="recovery-task", session_id="recovery-session",
    )
    context.trace_id = ""
    checkpoints = []

    async def snapshot(ctx, **kwargs):
        import copy
        checkpoints.append((copy.deepcopy(ctx.task_state.working_state.kv_store), kwargs))
        return SimpleNamespace(id="recovery-checkpoint")

    monkeypatch.setattr("aworld.core.context.amni.get_context_manager", lambda: SimpleNamespace(
        save_context_checkpoint=snapshot,
    ))
    return context, checkpoints


def _recovery_tools():
    from aworld.core.context.budget_recovery import READ_TOOL
    return [{"type": "function", "function": {
        "name": READ_TOOL,
        "parameters": {"type": "object", "properties": {
            "knowledge_id": {"type": "string"}, "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
        }, "required": ["knowledge_id", "start_line", "end_line"]},
    }}]


def _publish_stable_amni_prefix(context, model, content):
    from aworld.agents.final_context_adapter import adapt_amni_system_sections

    agent_id = model._context_agent_identity(context)
    context.publish_context_observation(ContextObservationSidecar.from_adapter_result(
        owner="amni.system_sections", namespace=agent_id, source_identity="amni-stable-prefix",
        result=adapt_amni_system_sections(
            sections=({"name": "system_prompt", "stability": "stable", "content": content},),
            source_identity="amni-stable-prefix", task_id=context.task_id,
            task_epoch=context.task_epoch, agent_id=agent_id,
        ), task_epoch=context.task_epoch,
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["sync", "async", "stream", "astream"])
async def test_total_budget_recovery_uses_amni_and_preserves_cache_prefix(tmp_path, monkeypatch, shape):
    import copy
    from aworld.core.context.budget_recovery import RECOVERY_STATE_KEY

    context, checkpoints = _recovery_context(tmp_path, monkeypatch)
    provider, calls = _azure_without_transport()
    model = LLMModel(
        conf=ModelConfig(context_compiler={"context_limit": 16000}), custom_provider=provider,
    )
    model.provider_name = "azure_openai"
    plans = []

    def compile_and_capture(**kwargs):
        candidate = compile_context_candidate(**kwargs)
        plans.append(candidate.final_result.cache_plan)
        return candidate

    monkeypatch.setattr("aworld.models.llm.compile_context_candidate", compile_and_capture)
    messages = _long_tool_exchange()
    original = copy.deepcopy(messages)
    tools = _recovery_tools()
    _publish_stable_amni_prefix(context, model, messages[0]["content"])
    model.completion(messages[:2], context=context, tools=tools)
    if shape == "sync":
        model.completion(messages, context=context, tools=tools)
    elif shape == "async":
        await model.acompletion(messages, context=context, tools=tools)
    elif shape == "stream":
        list(model.stream_completion(messages, context=context, tools=tools))
    else:
        [chunk async for chunk in model.astream_completion(messages, context=context, tools=tools)]

    assert len(calls) == 2  # failed compiles never invoke a model
    assert messages == original
    sent = provider._test_sent_params[-1]["messages"]
    assert sent[:2] == messages[:2]
    assert all(not item.get("tool_calls") and item["role"] != "tool" for item in sent)
    assert "context-history-" in sent[-1]["content"]
    assert sent[-1]["content"].startswith("<aworld-untrusted-data ")
    assert plans[0].logical_stable_prefix_hash == plans[1].logical_stable_prefix_hash
    assert plans[0].stable_message_count == plans[1].stable_message_count == 1
    assert plans[0].tool_catalog_hash == plans[1].tool_catalog_hash
    assert plans[0].cache_epoch == plans[1].cache_epoch == 0
    assert checkpoints[0][1] == {"cache_boundary": False}
    assert any(RECOVERY_STATE_KEY in key for key in checkpoints[0][0])
    receipt = context.get_llm_calls()[-1]["context_rollout"]["budget_recovery"][0]
    assert receipt["tokens_after"] < receipt["tokens_before"]
    assert "report data" not in repr(receipt)

    # Replayed Memory must reuse the exact same capsule, including after the
    # volatile runtime registry is lost and only AMNI WorkingState remains.
    from aworld.core.context.runtime_state import TaskRuntimeStateRegistry
    context._task_runtime_state_registry = TaskRuntimeStateRegistry()
    model.completion(messages, context=context, tools=tools)
    assert provider._test_sent_params[-1]["messages"] == sent
    assert len(checkpoints) == 1
    artifact = context._workspace.artifacts[0]
    read = await context.knowledge_service.get_knowledge_by_lines(artifact.artifact_id, 1, 8)
    assert "Lines 1-4" in read
    assert len(read) < 2500
    # Artifact stores every original argument byte, including long strings.
    archived = json.loads(artifact.content.replace("\n", ""))
    assert archived[0]["tool_calls"] == original[2]["tool_calls"]


@pytest.mark.asyncio
async def test_million_token_default_still_archives_and_restores_completed_history(tmp_path, monkeypatch):
    import copy
    from aworld.core.context.runtime_state import TaskRuntimeStateRegistry

    context, checkpoints = _recovery_context(tmp_path, monkeypatch)
    provider, calls = _azure_without_transport()
    provider.model_name = "unregistered-deployment-alias"
    provider.kwargs = {"params": {"max_completion_tokens": 32768}}
    model = LLMModel(conf=ModelConfig(), custom_provider=provider)
    model.provider_name = "azure_openai"
    assert model.resolve_context_window().tokens == 1_000_000
    assert model.resolve_context_window().source == "fallback"
    assert model._context_checkpoint_policy == "adaptive"
    assert model._context_artifact_offload is True

    messages = _long_tool_exchange()
    messages[2]["tool_calls"][0]["function"]["arguments"] = json.dumps({
        "content": "archive-data\n" * 350000,
    })
    constraint = {"role": "user", "content": "Keep the original input files unchanged."}
    messages.append(constraint)
    original = copy.deepcopy(messages)
    assert estimate_canonical_json_tokens(messages).value > 1_000_000
    tools = _recovery_tools()
    _publish_stable_amni_prefix(context, model, messages[0]["content"])
    await model.acompletion(messages, context=context, tools=tools)

    assert len(calls) == 1
    assert messages == original
    sent = provider._test_sent_params[-1]["messages"]
    assert sent[:2] == original[:2]
    assert sent[-1] == constraint
    assert "context-history-" in sent[-2]["content"]
    record = context.get_llm_calls()[-1]["context_rollout"]
    assert record["context_window_resolution"]["tokens"] == 1_000_000
    assert record["budget_recovery"][0]["status"] == "offloaded"
    assert record["budget_recovery"][0]["tokens_before"] > 1_000_000
    assert _reserved_output(context) == 32768
    assert len(checkpoints) == 1
    artifact = context._workspace.artifacts[0]
    restored = json.loads(artifact.content.replace("\n", ""))
    assert restored[0]["tool_calls"] == original[2]["tool_calls"]

    # Durable recovery also prevents the full history being reinserted on resume.
    context._task_runtime_state_registry = TaskRuntimeStateRegistry()
    await model.acompletion(messages, context=context, tools=tools)
    assert provider._test_sent_params[-1]["messages"] == sent
    assert len(checkpoints) == 1


@pytest.mark.asyncio
async def test_recovery_preserves_pending_calls_and_all_user_constraints(tmp_path, monkeypatch):
    from aworld.core.context.budget_recovery import recover_context_budget

    context, _ = _recovery_context(tmp_path, monkeypatch)
    messages = _long_tool_exchange()
    pending = {"role": "assistant", "content": "pending", "tool_calls": [{
        "id": "pending", "type": "function", "function": {"name": "write", "arguments": "{}"},
    }]}
    constraint = {"role": "user", "content": "Additional constraint: never change the input files."}
    messages.extend([constraint, pending])
    recovered, _ = await recover_context_budget(
        context=context, agent_id="agent", messages=messages, tools=_recovery_tools(),
    )
    assert recovered[:2] == messages[:2]
    assert recovered[-2:] == [constraint, pending]
    assert messages[2]["tool_calls"][0]["id"] == "call-large-report"


@pytest.mark.asyncio
async def test_recovery_storage_failure_never_substitutes_or_invokes_provider(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from aworld.core.context.budget_recovery import RECOVERY_STATE_KEY

    context, checkpoints = _recovery_context(tmp_path, monkeypatch)
    monkeypatch.setattr(context.knowledge_service, "get_knowledge_by_id", AsyncMock(return_value=None))
    provider, calls = _azure_without_transport()
    model = LLMModel(conf=ModelConfig(context_compiler={"context_limit": 16000}), custom_provider=provider)
    model.provider_name = "azure_openai"
    with pytest.raises(CandidateRequestNotEnforceable, match="required_context_budget_exceeded"):
        await model.acompletion(_long_tool_exchange(), context=context, tools=_recovery_tools())
    assert not calls
    assert not checkpoints
    assert context.read_task_runtime_state(model._context_agent_identity(context), RECOVERY_STATE_KEY) is None
    assert context.context_info["last_context_budget_recovery"][-1]["status"] == "failed"
    assert context.get_llm_calls()[-1]["context_rollout"]["budget_recovery"][-1]["error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_recovery_never_offloads_without_active_readback_capability(tmp_path, monkeypatch):
    from aworld.core.context.budget_recovery import recover_context_budget

    context, checkpoints = _recovery_context(tmp_path, monkeypatch)
    recovered, receipt = await recover_context_budget(
        context=context, agent_id="agent", messages=_long_tool_exchange(), tools=[],
    )
    assert recovered is None
    assert receipt["reason"] == "bounded_readback_tool_unavailable"
    assert not context._workspace.artifacts
    assert not checkpoints


def test_repeated_recovery_keeps_working_set_bounded_and_cache_prefix_stable(tmp_path, monkeypatch):
    context, checkpoints = _recovery_context(tmp_path, monkeypatch)
    provider, calls = _azure_without_transport()
    model = LLMModel(conf=ModelConfig(context_compiler={"context_limit": 16000}), custom_provider=provider)
    model.provider_name = "azure_openai"
    history = _long_tool_exchange()[:2]
    for index in range(12):
        exchange = _long_tool_exchange()[2:]
        exchange[0]["tool_calls"][0]["id"] = f"call-{index}"
        exchange[1]["tool_call_id"] = f"call-{index}"
        history.extend(exchange)
        model.completion(history, context=context, tools=_recovery_tools())
        sent = provider._test_sent_params[-1]["messages"]
        assert sent[:2] == history[:2]
        assert len(sent) == 3
        assert estimate_canonical_json_tokens(sent).value < 2000
        assert provider._test_sent_params[-1]["tools"] == _recovery_tools()
        assert context.context_lifecycle_state.checkpoint_revision == 0
    assert len(calls) == len(checkpoints) == 12


def test_recovery_retains_anthropic_native_prefix_cache_control(tmp_path, monkeypatch):
    context, checkpoints = _recovery_context(tmp_path, monkeypatch)
    provider, calls = _anthropic_without_transport()
    model = LLMModel(conf=ModelConfig(
        context_compiler={"context_limit": 16000},
        context_cache={"allow_provider_native_cache": True},
    ), custom_provider=provider)
    model.provider_name = "anthropic"
    messages = _long_tool_exchange()
    _publish_stable_amni_prefix(context, model, messages[0]["content"])
    model.completion(messages[:2], context=context, tools=_recovery_tools())
    model.completion(messages, context=context, tools=_recovery_tools())
    assert len(calls) == 2
    assert len(checkpoints) == 1
    assert calls[0]["system"] == calls[1]["system"]
    assert calls[1]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert calls[0]["tools"] == calls[1]["tools"]


@pytest.mark.asyncio
async def test_recovery_checkpoint_failure_rolls_back_substitution(tmp_path, monkeypatch):
    from aworld.core.context.budget_recovery import recover_context_budget_bounded, RECOVERY_STATE_KEY

    context, _ = _recovery_context(tmp_path, monkeypatch)

    async def fail_snapshot(**kwargs):
        raise OSError("checkpoint unavailable")

    monkeypatch.setattr(context, "snapshot", fail_snapshot)
    messages = _long_tool_exchange()
    recovered, receipt = await recover_context_budget_bounded(
        context=context, agent_id="agent", messages=messages, tools=_recovery_tools(),
    )
    assert recovered is None
    assert receipt == {"status": "failed", "error_type": "OSError"}
    assert context.read_task_runtime_state("agent", RECOVERY_STATE_KEY)["replacements"] == []
    assert context.get(f"{RECOVERY_STATE_KEY}:agent")["replacements"] == []
    assert messages[2]["tool_calls"][0]["id"] == "call-large-report"


@pytest.mark.asyncio
async def test_recovery_keeps_incomplete_parallel_and_legacy_calls(tmp_path, monkeypatch):
    from aworld.core.context.budget_recovery import recover_context_budget

    context, _ = _recovery_context(tmp_path, monkeypatch)
    messages = _long_tool_exchange()
    messages[2]["tool_calls"].append({
        "id": "pending", "type": "function", "function": {"name": "wait", "arguments": "{}"},
    })
    messages.append({"role": "assistant", "function_call": {"name": "pending", "arguments": "{}"}})
    recovered, receipt = await recover_context_budget(
        context=context, agent_id="agent", messages=messages, tools=_recovery_tools(),
    )
    assert recovered is None
    assert receipt["reason"] == "no_completed_exchange"
    assert not context._workspace.artifacts


@pytest.mark.parametrize("config", [
    {"max_item_tokens": 10000}, {"artifact_offload": False}, {"checkpoint_policy": "explicit"},
])
def test_recovery_respects_explicit_policy_contracts(tmp_path, monkeypatch, config):
    context, checkpoints = _recovery_context(tmp_path, monkeypatch)
    provider, calls = _azure_without_transport()
    model = LLMModel(
        conf=ModelConfig(context_compiler={"context_limit": 16000, **config}), custom_provider=provider,
    )
    model.provider_name = "azure_openai"
    with pytest.raises(CandidateRequestNotEnforceable):
        model.completion(_long_tool_exchange(), context=context, tools=_recovery_tools())
    assert not calls
    assert not checkpoints
    assert not context._workspace.artifacts


def test_adaptive_agent_provides_only_bounded_readback_without_cognitive_ingestion(tmp_path, monkeypatch):
    from aworld.agents.llm_agent import LLMAgent
    from aworld.core.context.budget_recovery import READ_TOOL

    context, _ = _recovery_context(tmp_path, monkeypatch)
    agent = LLMAgent.__new__(LLMAgent)
    agent._llm = SimpleNamespace(
        context_compiler_mode=ContextCompilerMode.ENFORCE,
        _context_checkpoint_policy="adaptive", _context_artifact_offload=True,
    )
    agent.black_tool_actions = {}
    schema = agent._context_budget_recovery_tool(context)
    assert schema["function"]["name"] == READ_TOOL
    assert set(schema["function"]["parameters"]["properties"]) == {"knowledge_id", "start_line", "end_line"}
    agent.black_tool_actions = {"KNOWLEDGE": ["get_knowledge_by_lines"]}
    assert agent._context_budget_recovery_tool(context) is None
    agent.black_tool_actions = {}
    agent._llm._context_artifact_offload = False
    assert agent._context_budget_recovery_tool(context) is None


@pytest.mark.asyncio
async def test_configured_output_budget_reaches_every_direct_call_shape():
    class TokenRecordingProvider(CountingProvider):
        def __init__(self) -> None:
            super().__init__()
            self.max_tokens_seen: list[tuple[str, int | None]] = []

        def _record(self, kind: str, messages: Any, kwargs: dict[str, Any]) -> None:
            super()._record(kind, messages, kwargs)
            self.max_tokens_seen.append((kind, kwargs.get("max_tokens")))

    provider = TokenRecordingProvider()
    model = LLMModel(
        conf=ModelConfig(max_tokens=6144, context_compiler={"mode": "off"}),
        custom_provider=provider,
    )
    messages = [{"role": "user", "content": "go"}]

    model.completion(messages)
    await model.acompletion(messages)
    list(model.stream_completion(messages))
    assert [item async for item in model.astream_completion(messages)]

    assert provider.max_tokens_seen == [
        ("completion", 6144),
        ("acompletion", 6144),
        ("stream_completion", 6144),
        ("astream_completion", 6144),
    ]


def _output_reservation_model(monkeypatch, **config):
    provider, calls = _azure_without_transport()

    def create_provider(model, **kwargs):
        # Exercise ModelConfig -> provider params with a fake SDK transport.
        provider.kwargs = kwargs
        model.provider = provider

    monkeypatch.setattr(LLMModel, "_create_provider", create_provider)
    compiler_config = {
        "context_limit": 40000,
        "checkpoint_policy": "explicit",
        **config.pop("context_compiler", {}),
    }
    model = LLMModel(conf=ModelConfig(
        llm_provider="azure_openai", llm_model_name="azure-test",
        context_compiler=compiler_config, **config,
    ))
    return model, provider, calls


async def _invoke_output_reservation_shape(model, shape, messages, context, **kwargs):
    if shape == "sync":
        return model.completion(messages, context=context, **kwargs)
    if shape == "async":
        return await model.acompletion(messages, context=context, **kwargs)
    if shape == "stream":
        return list(model.stream_completion(messages, context=context, **kwargs))
    return [chunk async for chunk in model.astream_completion(
        messages, context=context, **kwargs,
    )]


def _output_reservation_context(name):
    context = Context(task_id=name)
    context.trace_id = ""
    return context


def _reserved_output(context):
    return context.get_llm_calls()[-1]["context_rollout"]["final_compile"]["tokens"][
        "reserved_output"
    ]["value"]


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["sync", "async", "stream", "astream"])
@pytest.mark.parametrize("config,request_kwargs,wire_key", [
    ({"max_tokens": 32768}, {}, "max_tokens"),
    ({"params": {"max_completion_tokens": 32768}}, {}, "max_completion_tokens"),
    ({}, {"max_tokens": 32768}, "max_tokens"),
    ({}, {"max_completion_tokens": 32768}, "max_completion_tokens"),
])
async def test_effective_output_reservation_blocks_overflow_before_every_call_shape(
    monkeypatch, shape, config, request_kwargs, wire_key,
):
    model, provider, calls = _output_reservation_model(monkeypatch, **config)
    shared_policy = model.context_candidate_policy
    # Fits the former 4096-token output reserve, but not the requested 32768.
    large_messages = [{"role": "user", "content": "x" * 32000}]
    blocked = _output_reservation_context(f"output-overflow-{shape}")
    with pytest.raises(CandidateRequestNotEnforceable, match="required_context_budget_exceeded"):
        await _invoke_output_reservation_shape(
            model, shape, large_messages, blocked, **request_kwargs,
        )
    assert calls == []
    assert blocked.get_llm_calls()[-1]["status"] == "blocked_before_provider"
    assert blocked.get_llm_calls()[-1]["provider_invoked"] is False
    assert large_messages[0]["content"] == "x" * 32000

    valid = _output_reservation_context(f"output-valid-{shape}")
    await _invoke_output_reservation_shape(
        model, shape, [{"role": "user", "content": "go"}], valid, **request_kwargs,
    )
    assert len(calls) == 1
    assert provider._test_sent_params[-1][wire_key] == 32768
    assert _reserved_output(valid) == 32768
    assert model.context_candidate_policy is shared_policy


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["sync", "async", "stream", "astream"])
@pytest.mark.parametrize("config,request_kwargs,expected_reserve,wire_limits", [
    ({"max_tokens": 32768}, {"max_tokens": 8192}, 8192, {"max_tokens": 8192}),
    ({"params": {"max_completion_tokens": 32768}}, {"max_completion_tokens": 8192},
     8192, {"max_completion_tokens": 8192}),
    ({"params": {"max_completion_tokens": 32768}}, {"max_completion_tokens": None},
     4096, {}),
    # The named max_tokens=None already removes this configured parameter at
    # the OpenAI boundary; budget inference must not reintroduce it.
    ({"params": {"max_tokens": 32768}}, {}, 4096, {}),
    ({"max_tokens": 4096, "params": {"max_completion_tokens": 16384}},
     {"max_tokens": 8192}, 16384, {"max_tokens": 8192, "max_completion_tokens": 16384}),
    ({"context_compiler": {"reserved_output_tokens": 12288}}, {"max_tokens": 8192},
     12288, {"max_tokens": 8192}),
])
async def test_output_reservation_matches_provider_precedence_and_explicit_minimum(
    monkeypatch, shape, config, request_kwargs, expected_reserve, wire_limits,
):
    model, provider, calls = _output_reservation_model(monkeypatch, **config)
    shared_policy = model.context_candidate_policy
    context = _output_reservation_context(f"output-precedence-{shape}")
    # A smaller per-call override must also reclaim input space from a larger
    # configured default, rather than just changing the outbound parameter.
    messages = [{"role": "user", "content": "x" * 32000}]
    await _invoke_output_reservation_shape(model, shape, messages, context, **request_kwargs)
    assert len(calls) == 1
    assert _reserved_output(context) == expected_reserve
    sent = provider._test_sent_params[-1]
    assert {key: sent[key] for key in ("max_tokens", "max_completion_tokens") if key in sent} == wire_limits
    assert sent["messages"] == messages
    assert model.context_candidate_policy is shared_policy


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["async", "astream"])
async def test_concurrent_output_reservations_remain_request_local(monkeypatch, shape):
    model, provider, calls = _output_reservation_model(
        monkeypatch, params={"max_completion_tokens": 32768},
    )
    shared_policy = model.context_candidate_policy
    first_entered, both_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    create = provider.async_provider.chat.completions.create

    async def wait_for_both(**kwargs):
        response = await create(**kwargs)
        first_entered.set()
        if len(calls) == 2:
            both_entered.set()
        await release.wait()
        return response

    provider.async_provider.chat.completions.create = wait_for_both
    high = _output_reservation_context(f"output-concurrent-high-{shape}")
    low = _output_reservation_context(f"output-concurrent-low-{shape}")
    high_call = asyncio.create_task(_invoke_output_reservation_shape(
        model, shape, [{"role": "user", "content": "go"}], high,
    ))
    tasks = [high_call]
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=5)
        tasks.append(asyncio.create_task(_invoke_output_reservation_shape(
            model, shape, [{"role": "user", "content": "x" * 32000}], low,
            max_completion_tokens=8192,
        )))
        await asyncio.wait_for(both_entered.wait(), timeout=5)
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert _reserved_output(high) == 32768
    assert _reserved_output(low) == 8192
    assert [item["max_completion_tokens"] for item in provider._test_sent_params] == [32768, 8192]
    assert model.context_candidate_policy is shared_policy
    # An unset reserve is not a hard floor; the fallback is request-local.
    assert shared_policy.final_policy.input_budget.reserved_output_tokens == 0


def test_output_reservation_larger_than_context_is_blocked_before_provider(monkeypatch):
    model, _provider, calls = _output_reservation_model(
        monkeypatch, context_compiler={"checkpoint_policy": "adaptive"},
    )
    recovery_calls = []

    async def recover(**kwargs):
        recovery_calls.append(kwargs)
        return None, {"status": "failed"}

    monkeypatch.setattr(
        "aworld.core.context.budget_recovery.recover_context_budget_bounded", recover,
    )
    with pytest.raises(CandidateRequestNotEnforceable, match="required_context_budget_exceeded"):
        model.completion(
            [{"role": "user", "content": "go"}], max_completion_tokens=40000,
            context=_output_reservation_context("output-reserve-exceeds-context"),
        )
    assert calls == []
    assert recovery_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["sync", "async", "stream", "astream"])
async def test_output_reservation_includes_native_provider_default(shape):
    provider, calls = _anthropic_without_transport()
    model = LLMModel(
        conf=ModelConfig(context_compiler={"reserved_output_tokens": 128}),
        custom_provider=provider,
    )
    model.provider_name = "anthropic"
    context = _output_reservation_context(f"output-native-default-{shape}")
    await _invoke_output_reservation_shape(
        model, shape, [{"role": "user", "content": "go"}], context,
    )
    assert len(calls) == 1
    assert calls[-1]["max_tokens"] == 4096
    assert _reserved_output(context) == 4096


def _adaptive_window_model(monkeypatch, name, **config):
    provider, calls = _azure_without_transport()

    def create_provider(model, **kwargs):
        provider.kwargs = kwargs
        provider.model_name = kwargs["model_name"]
        model.provider = provider

    monkeypatch.setattr(LLMModel, "_create_provider", create_provider)
    candidate_policy = config.pop("candidate_policy", None)
    compiler = {"checkpoint_policy": "explicit", **config.pop("context_compiler", {})}
    model = LLMModel(conf=ModelConfig(
        llm_provider="azure_openai", llm_model_name=name,
        context_compiler=compiler, **config,
    ), context_candidate_policy=candidate_policy)
    return model, provider, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["sync", "async", "stream", "astream"])
async def test_registered_million_window_accepts_more_than_128k_input(monkeypatch, shape):
    model, provider, calls = _adaptive_window_model(
        monkeypatch, "gemini-2.5-pro", params={"max_completion_tokens": 32768},
    )
    messages = [{"role": "user", "content": "x" * 600000}]
    assert estimate_canonical_json_tokens(messages).value > 128000
    context = _output_reservation_context(f"million-window-{shape}")
    await _invoke_output_reservation_shape(model, shape, messages, context)
    receipt = context.get_llm_calls()[-1]["context_rollout"]
    assert calls
    assert provider._test_sent_params[-1]["messages"] == messages
    assert receipt["context_window_resolution"]["source"] == "model_registry"
    assert receipt["final_compile"]["tokens"]["context_limit"]["value"] == 1048576
    assert _reserved_output(context) == 32768
    assert model._context_input_budget == 1048576 - 32768 - 256 - 512


def test_per_call_model_switch_does_not_inherit_previous_deployment_window(monkeypatch):
    model, provider, calls = _adaptive_window_model(
        monkeypatch, "gateway-alias", max_model_len=1000000,
        params={"max_completion_tokens": 1024},
    )
    shared = model.context_candidate_policy
    blocked = _output_reservation_context("switch-small")
    with pytest.raises(CandidateRequestNotEnforceable, match="required_context_budget_exceeded"):
        model.completion(
            [{"role": "user", "content": "x" * 40000}],
            model_name="gpt-4", context=blocked,
        )
    assert calls == []
    resolution = blocked.get_llm_calls()[-1]["context_rollout"]["context_window_resolution"]
    assert resolution["tokens"] == 8192
    assert resolution["source"] == "model_registry"
    model.completion(
        [{"role": "user", "content": "x" * 600000}],
        context=_output_reservation_context("switch-original"),
    )
    assert len(calls) == 1
    assert provider._test_sent_params[-1]["model"] == "gateway-alias"
    assert model.context_candidate_policy is shared
    assert shared.final_policy.input_budget.context_limit == 1000000


@pytest.mark.parametrize("params,request_args", [
    ({"model": "gpt-4", "max_completion_tokens": 1024}, {}),
    ({"max_completion_tokens": 1024}, {"model_name": "gemini-2.5-pro", "model": "gpt-4"}),
])
def test_model_resolution_matches_provider_override_and_inference_profile(monkeypatch, params, request_args):
    model, provider, calls = _adaptive_window_model(monkeypatch, "gemini-2.5-pro", params=params)
    import aworld.models.llm as module
    compile_request = module.compile_context_candidate
    profiles = []

    def capture(**kwargs):
        profiles.append(kwargs["compiler_input"].inference_profile)
        return compile_request(**kwargs)

    monkeypatch.setattr(module, "compile_context_candidate", capture)
    context = _output_reservation_context("model-override")
    model.completion([{"role": "user", "content": "go"}], context=context, **request_args)
    resolution = context.get_llm_calls()[-1]["context_rollout"]["context_window_resolution"]
    assert provider._test_sent_params[-1]["model"] == profiles[-1].model == resolution["model_name"] == "gpt-4"
    assert profiles[-1].context_limit == resolution["tokens"] == 8192
    with pytest.raises(CandidateRequestNotEnforceable, match="required_context_budget_exceeded"):
        model.completion(
            [{"role": "user", "content": "x" * 40000}],
            context=_output_reservation_context("model-override-overflow"), **request_args,
        )
    assert len(calls) == 1


def test_small_model_with_small_output_does_not_inherit_implicit_4096_floor(monkeypatch):
    model, provider, calls = _adaptive_window_model(monkeypatch, "llama-2", max_tokens=1024)
    context = _output_reservation_context("small-window")
    model.completion([{"role": "user", "content": "go"}], context=context)
    assert calls
    assert provider._test_sent_params[-1]["max_tokens"] == 1024
    assert _reserved_output(context) == 1024
    assert model._context_input_budget == 4096 - 1024 - 256 - 512


@pytest.mark.parametrize("request_args,reserve", [({}, 4096), ({"max_completion_tokens": 1024}, 1024)])
def test_readonly_budget_without_final_compiler_handles_default_and_small_caps(monkeypatch, request_args, reserve):
    model, _provider, calls = _adaptive_window_model(
        monkeypatch, "gateway-alias", context_compiler={"universal_final": False},
    )
    budget = model.resolve_request_context_budget(request_args)
    assert budget.context_limit == 1000000
    assert budget.reserved_output_tokens == reserve
    assert budget.available_input_tokens == 1000000 - reserve - 256 - 512
    assert calls == []


def test_explicit_final_policy_controls_initial_hint_and_request_budget(monkeypatch):
    from aworld.core.context.compiler import ContextInputBudget, FinalCompilePolicy
    supplied = CandidateCompilePolicy(final_policy=FinalCompilePolicy(
        compiler_version="test", policy_version="test",
        input_budget=ContextInputBudget(
            context_limit=500000, reserved_output_tokens=256,
            provider_protocol_reserve=256, safety_margin_tokens=512,
        ),
    ))
    model, _provider, calls = _adaptive_window_model(
        monkeypatch, "gpt-4", candidate_policy=supplied,
        params={"max_completion_tokens": 32768},
    )
    assert model._context_input_budget == 500000 - 32768 - 256 - 512
    budget = model.resolve_request_context_budget({"model": "other-model", "max_completion_tokens": 1024})
    assert budget.context_limit == 500000
    assert budget.reserved_output_tokens == 1024
    assert model.context_candidate_policy is supplied
    from dataclasses import replace
    updated = replace(supplied, final_policy=replace(
        supplied.final_policy,
        input_budget=replace(supplied.final_policy.input_budget, context_limit=700000),
    ))
    model.configure_context_compiler(mode=model.context_compiler_mode, candidate_policy=updated)
    assert model._context_input_budget == 700000 - 32768 - 256 - 512
    assert model.resolve_request_context_budget().context_limit == 700000
    assert calls == []


def test_unknown_sdk_target_does_not_reuse_deployment_provenance(monkeypatch):
    model, _provider, calls = _adaptive_window_model(monkeypatch, "deployment-alias", max_model_len=1000000)
    unknown = model.resolve_context_window({"extra_body": {"model": None}})
    assert unknown.model_name is None
    assert unknown.source == "fallback"
    assert model.resolve_context_window().source == "explicit_max_model_len"
    assert calls == []


def test_sdk_output_override_is_rejected_before_provider_when_it_exceeds_window(monkeypatch):
    model, _provider, calls = _adaptive_window_model(monkeypatch, "deployment-alias", max_model_len=1000000)
    context = _output_reservation_context("sdk-output-overflow")
    with pytest.raises(CandidateRequestNotEnforceable, match="required_context_budget_exceeded"):
        model.completion(
            [{"role": "user", "content": "go"}], context=context,
            extra_body={"max_completion_tokens": 1000001},
        )
    assert calls == []
    assert context.get_llm_calls()[-1]["status"] == "blocked_before_provider"


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
        lambda self, chunk, **kwargs: (CountingProvider._response("stream"), "stop"),
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
async def test_anthropic_universal_cache_plan_lowers_native_boundary_all_paths():
    provider, calls = _anthropic_without_transport()
    model = LLMModel(
        conf=ModelConfig(
            context_cache={"allow_provider_native_cache": True},
            context_compiler={"mode": "enforce", "universal_final": True},
        ),
        custom_provider=provider,
    )
    model.provider_name = "anthropic"
    contexts = [Context(task_id=f"anthropic-cache-{index}") for index in range(4)]
    for context in contexts:
        context.trace_id = ""
        context.advance_context_lifecycle(LifecycleAction.CHECKPOINT)
    messages = [
        {"role": "system", "content": "stable rules"},
        {"role": "user", "content": "dynamic request"},
    ]

    await model.acompletion(messages, context=contexts[0])
    model.completion(messages, context=contexts[1])
    list(model.stream_completion(messages, context=contexts[2]))
    [chunk async for chunk in model.astream_completion(messages, context=contexts[3])]

    assert len(calls) == 4
    for call, context in zip(calls, contexts, strict=True):
        assert call["system"] == [
            {
                "type": "text",
                "text": "stable rules",
                "cache_control": {"type": "ephemeral"},
            }
        ]
        record = context.get_llm_calls()[0]
        candidate = record["context_rollout"]["candidate_snapshot"]
        lowering = record["context_rollout"]["provider_lowering"]
        assert lowering["cache_plan_fingerprint"] == candidate[
            "cache_plan_fingerprint"
        ]
        assert lowering["candidate_contract_hash"] == candidate[
            "candidate_contract_hash"
        ]
        assert lowering["cache_lowering_status"] == "applied"
        assert lowering["cache_lowering_strategy"] == "anthropic_cache_control"
        cache_plan = record["context_rollout"]["final_compile"]["cache_plan"]
        assert cache_plan["cache_epoch"] == 1
        assert cache_plan["break_reasons"] == ["history_compaction"]
        assert context.get_pending_cache_break_reasons() == ()


def test_anthropic_universal_cache_plan_keeps_native_control_off_by_default():
    provider, calls = _anthropic_without_transport()
    model = LLMModel(
        conf=ModelConfig(
            context_compiler={"mode": "enforce", "universal_final": True},
        ),
        custom_provider=provider,
    )
    model.provider_name = "anthropic"
    context = Context(task_id="anthropic-cache-default-off")
    context.trace_id = ""

    model.completion(
        [
            {"role": "system", "content": "stable rules"},
            {"role": "user", "content": "dynamic request"},
        ],
        context=context,
    )

    assert calls[-1]["system"] == "stable rules"
    record = context.get_llm_calls()[0]
    cache_plan = record["context_rollout"]["final_compile"]["cache_plan"]
    assert cache_plan["native_cache_requested"] is False
    assert cache_plan["stable_message_count"] == 1
    assert cache_plan["logical_stable_prefix_hash"]
    lowering = record["context_rollout"]["provider_lowering"]
    assert lowering["cache_lowering_status"] == "disabled"
    assert lowering["cache_lowering_strategy"] == "explicit_opt_out"


def test_custom_anthropic_endpoint_requires_native_cache_capability():
    provider, calls = _anthropic_without_transport()
    provider.base_url = "https://anthropic-compatible.example.test/v1"
    model = LLMModel(
        conf=ModelConfig(
            context_cache={"allow_provider_native_cache": True},
            context_compiler={"mode": "enforce", "universal_final": True},
        ),
        custom_provider=provider,
    )
    model.provider_name = "anthropic"
    context = Context(task_id="anthropic-custom-cache-capability")
    context.trace_id = ""
    messages = [
        {"role": "system", "content": "stable rules"},
        {"role": "user", "content": "dynamic request"},
    ]

    model.completion(messages, context=context)

    assert calls[-1]["system"] == "stable rules"
    lowering = context.get_llm_calls()[0]["context_rollout"]["provider_lowering"]
    assert lowering["cache_lowering_status"] == "unsupported"
    assert lowering["cache_lowering_strategy"] == "provider_capability_not_declared"

    provider.kwargs["provider_native_cache_capability"] = "supported"
    supported_context = Context(task_id="anthropic-custom-cache-opt-in")
    supported_context.trace_id = ""
    model.completion(messages, context=supported_context)

    assert calls[-1]["system"] == [
        {
            "type": "text",
            "text": "stable rules",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    supported = supported_context.get_llm_calls()[0]["context_rollout"][
        "provider_lowering"
    ]
    assert supported["cache_lowering_status"] == "applied"


def test_unsupported_native_cache_provider_reports_evidence_without_blocking():
    provider, calls = _ant_without_transport()
    model = LLMModel(
        conf=ModelConfig(
            context_compiler={"mode": "enforce", "universal_final": True}
        ),
        custom_provider=provider,
    )
    model.provider_name = "ant"
    context = Context(task_id="ant-cache-unsupported")
    context.trace_id = ""

    model.completion(
        [
            {"role": "system", "content": "stable rules"},
            {"role": "user", "content": "go"},
        ],
        context=context,
    )

    assert len(calls) == 1
    lowering = context.get_llm_calls()[0]["context_rollout"]["provider_lowering"]
    assert lowering["cache_lowering_status"] == "unsupported"
    assert lowering["cache_lowering_strategy"] == "none"


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
