import asyncio
from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.common import TaskStatusValue
from aworld.core.context.amni.config import AgentContextConfig, ContextCacheConfig
from aworld.core.context.amni.prompt.assembly import PromptAssemblyPlan
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    ContextObservationSidecar,
    adapt_final_messages,
)
from aworld.core.context.context_state import ContextState
from aworld.core.event.base import Constants, Message
from aworld.core.task import Task
from aworld.core.exceptions import AWorldRuntimeException
from aworld.models.llm import AWORLD_CONTEXT_CALL_ID_KWARG
from aworld.models.model_response import ModelResponse


def _build_agent(name: str = "Aworld") -> Agent:
    return Agent(
        name=name,
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )


def _build_context(task_id: str = "task-1") -> Context:
    context = Context(task_id=task_id)
    context.set_task(Task(id=task_id, name="test-task"))
    return context


@pytest.mark.asyncio
async def test_llm_call_records_append_without_mutating_parent_state():
    agent = _build_agent()
    parent_context = _build_context()
    parent_context.context_info["llm_calls"] = [
        {
            "call_id": "parent-call",
            "request": {"messages": [{"role": "system", "content": "parent"}]},
        }
    ]

    child_context = _build_context()
    child_context.context_info = ContextState(parent_state=parent_context.context_info)
    message = Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        headers={"context": child_context},
    )

    call_id = agent._record_llm_call_request(
        message,
        [{"role": "user", "content": "hello"}],
        started_at="2026-05-06T12:00:00",
    )
    response = ModelResponse(
        id="resp-1",
        model="fake-model",
        content="done",
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    )
    agent._record_llm_call_response(message, call_id, response)

    child_calls = message.context.context_info["llm_calls"]
    assert [record["call_id"] for record in child_calls] == ["parent-call", call_id]
    assert child_calls[-1]["request"]["messages"] == [{"role": "user", "content": "hello"}]
    assert child_calls[-1]["usage"]["total_tokens"] == 5
    assert message.context.context_info["llm_input"] == [{"role": "user", "content": "hello"}]
    assert message.context.context_info["llm_call_start_time"] == "2026-05-06T12:00:00"
    assert message.context.context_info["llm_output"] is response

    assert parent_context.context_info["llm_calls"] == [
        {
            "call_id": "parent-call",
            "request": {"messages": [{"role": "system", "content": "parent"}]},
        }
    ]


def test_prompt_assembly_observability_metadata_is_attached_to_call_record():
    agent = _build_agent()
    context = _build_context()
    message = Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        headers={"context": context},
    )

    call_id = agent._record_llm_call_request(
        message,
        [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
        ],
        started_at="2026-05-06T12:00:00",
    )

    agent._update_llm_call_observability(
        message,
        call_id,
        metadata=agent._build_prompt_assembly_observability(
            messages=[
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "hello"},
            ],
            tools=[{"function": {"name": "search", "parameters": {"type": "object"}}}],
            request_kwargs={"prompt_cache_key": "cache-key-1"},
        ),
    )

    record = message.context.context_info["llm_calls"][-1]
    observability = record["assembly_observability"]
    assert observability["assembly_provider"] == "DefaultPromptAssemblyProvider"
    assert observability["provider_name"] == "openai"
    assert observability["cache_aware_assembly"] is False
    assert observability["provider_native_cache"] is True
    assert observability["stable_prefix_hash"]


def test_prompt_assembly_observability_includes_only_redacted_owner_sidecars():
    agent = _build_agent()
    context = _build_context("task-owner-sidecar")
    result = adapt_final_messages(
        [{"role": "system", "content": "private-neuron-output"}],
        source_identity="owner://private/neuron/path",
    )
    context.publish_context_observation(
        ContextObservationSidecar.from_adapter_result(
            owner="amni.neuron_outputs",
            namespace=agent.id(),
            source_identity="owner://private/neuron/path",
            result=result,
        )
    )

    observability = agent._build_prompt_assembly_observability(
        context=context,
        messages=[{"role": "user", "content": "hello"}],
    )

    sidecars = observability["context_observations"]
    assert len(sidecars) == 1
    assert sidecars[0]["owner"] == "amni.neuron_outputs"
    rendered = str(sidecars)
    assert "private-neuron-output" not in rendered
    assert "owner://private/neuron/path" not in rendered


def test_llm_call_response_upgrades_native_cache_flag_when_cache_tokens_exist():
    agent = _build_agent()
    context = _build_context()
    message = Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        headers={"context": context},
    )

    call_id = agent._record_llm_call_request(
        message,
        [{"role": "user", "content": "hello"}],
        started_at="2026-05-06T12:00:00",
    )
    agent._update_llm_call_observability(
        message,
        call_id,
        metadata={
            "assembly_provider": "DefaultPromptAssemblyProvider",
            "provider_name": "anthropic",
            "cache_aware_assembly": False,
            "provider_native_cache": False,
        },
    )

    response = ModelResponse(
        id="resp-1",
        model="claude-sonnet",
        content="done",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cache_hit_tokens": 80,
        },
    )
    agent._record_llm_call_response(message, call_id, response)

    record = message.context.context_info["llm_calls"][-1]
    assert record["assembly_observability"]["provider_native_cache"] is True


def test_usage_has_cache_tokens_coerces_string_values():
    agent = _build_agent()

    assert agent._usage_has_cache_tokens({"cache_hit_tokens": "0", "cache_write_tokens": "1"}) is True
    assert agent._usage_has_cache_tokens({"cache_hit_tokens": "0", "cache_write_tokens": "0"}) is False


def test_prompt_assembly_observability_uses_injected_prompt_assembly_provider():
    class CustomPromptAssemblyProvider:
        def build_plan(self, *, messages, tools=None, metadata=None):
            observability = dict(metadata or {})
            observability["assembly_provider"] = "CustomPromptAssemblyProvider"
            observability["stable_prefix_hash"] = "custom-stable-hash"
            return PromptAssemblyPlan(
                messages=messages,
                stable_hash="custom-stable-hash",
                observability=observability,
                metadata=dict(metadata or {}),
            )

    agent = _build_agent()
    agent.prompt_assembly_provider = CustomPromptAssemblyProvider()

    observability = agent._build_prompt_assembly_observability(
        messages=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
        ],
        tools=[{"function": {"name": "search"}}],
        request_kwargs={"prompt_cache_key": "cache-key-1"},
    )

    assert observability["assembly_provider"] == "CustomPromptAssemblyProvider"
    assert observability["stable_prefix_hash"] == "custom-stable-hash"
    assert observability["provider_native_cache"] is True


def test_openai_prompt_assembly_observability_enables_native_cache_from_stable_prefix():
    class CustomPromptAssemblyProvider:
        def build_plan(self, *, messages, tools=None, metadata=None):
            observability = dict(metadata or {})
            observability["assembly_provider"] = "CustomPromptAssemblyProvider"
            return PromptAssemblyPlan(
                messages=messages,
                stable_hash="stable-hash-from-plan",
                observability=observability,
                metadata=dict(metadata or {}),
            )

    agent = _build_agent()
    agent.prompt_assembly_provider = CustomPromptAssemblyProvider()

    observability = agent._build_prompt_assembly_observability(
        messages=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
        ],
        tools=[{"function": {"name": "search"}}],
        request_kwargs={},
    )

    assert observability["provider_native_cache"] is True
    assert observability["stable_prefix_hash"] == "stable-hash-from-plan"
    assert observability["prompt_cache_key"] == "stable-hash-from-plan"


@pytest.mark.asyncio
async def test_async_policy_does_not_forward_prompt_cache_kwargs_to_unknown_provider():
    captured = {}

    class CapturingAgent(Agent):
        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "hello"},
            ]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = dict(kwargs)
            return ModelResponse(
                id="resp-1",
                model="fake-model",
                content="done",
                usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            )

    agent = CapturingAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="custom",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )
    context = _build_context()
    message = Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        headers={"context": context},
    )

    await agent.async_policy(
        SimpleNamespace(observer="user", from_agent_name=None, context=None),
        message=message,
    )

    assert captured["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "hello"},
    ]
    assert "prompt_assembly_plan" not in captured["kwargs"]
    assert "provider_native_prompt_cache" not in captured["kwargs"]
    assert AWORLD_CONTEXT_CALL_ID_KWARG not in captured["kwargs"]
    assert context.get_llm_calls()[0]["call_id"]


def test_enforce_compiles_after_assembly_without_replaying_provider_plan():
    assert Agent._forward_legacy_prompt_assembly_plan("openai", "off") is True
    assert Agent._forward_legacy_prompt_assembly_plan("openai", "shadow") is True
    assert Agent._forward_legacy_prompt_assembly_plan("openai", "enforce") is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "progressive_tools", "base_tools", "expected_names"),
    [
        ("enforce", True, None, ["write", "read"]),
        ("enforce", True, (), []),
        ("enforce", True, ("read",), ["read"]),
        ("enforce", False, ("read",), ["write", "read"]),
        ("observe", True, ("read",), ["write", "read"]),
        ("shadow", True, ("read",), ["write", "read"]),
    ],
)
async def test_progressive_catalog_requires_explicit_enforce_opt_in(
    monkeypatch, mode, progressive_tools, base_tools, expected_names
):
    captured = {}
    schemas = [
        {"type": "function", "function": {"name": "write"}},
        {"type": "function", "function": {"name": "read"}},
    ]

    class CapturingAgent(Agent):
        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": "hello"}]

        async def _filter_tools(self, context=None):
            return schemas

        async def invoke_model(self, messages=None, message=None, **kwargs):
            captured["tools"] = kwargs.get("prepared_tools")
            return ModelResponse(
                id="resp-progressive-tools",
                model="fake-model",
                content="done",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )

    agent = CapturingAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="custom",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )
    agent._llm = SimpleNamespace(
        context_compiler_mode=mode,
        _context_progressive_skills=False,
        _context_progressive_tools=progressive_tools,
        _context_progressive_tool_base_tools=base_tools,
        _context_task_catalog_policy="sticky",
        _context_artifact_offload=True,
        enforced_tool_output_policy=None,
    )
    async def skip_memory(*args, **kwargs):
        return None

    monkeypatch.setattr(agent, "_add_message_to_memory", skip_memory)
    context = _build_context("task-progressive-tools")
    message = Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        headers={"context": context},
    )

    await agent.async_policy(
        SimpleNamespace(observer="user", from_agent_name=None, context=None),
        message=message,
    )

    actual = captured["tools"] or []
    assert [schema["function"]["name"] for schema in actual] == expected_names


@pytest.mark.asyncio
async def test_progressive_catalog_preserves_new_unmanaged_mcp_namespace(monkeypatch):
    captured = {}
    schemas = [
        {"type": "function", "function": {"name": "run_code"}},
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "browser_navigate"}},
        {"type": "function", "function": {"name": "browser_snapshot"}},
    ]

    class CapturingAgent(Agent):
        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": "research a question"}]

        async def _filter_tools(self, context=None):
            self.tool_mapping = {
                "run_code": "docker__run_code",
                "read_file": "docker__read_file",
                "browser_navigate": "ms-playwright__browser_navigate",
                "browser_snapshot": "ms-playwright__browser_snapshot",
            }
            return schemas

        async def invoke_model(self, messages=None, message=None, **kwargs):
            captured["tools"] = kwargs.get("prepared_tools")
            return ModelResponse(
                id="resp-progressive-unmanaged-tools",
                model="fake-model",
                content="done",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )

    agent = CapturingAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="custom",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )
    agent._llm = SimpleNamespace(
        context_compiler_mode="enforce",
        _context_progressive_skills=False,
        _context_progressive_tools=True,
        _context_progressive_tool_base_tools=("run_code",),
        _context_progressive_tool_unmanaged_policy="preserve",
        _context_task_catalog_policy="sticky",
        _context_artifact_offload=True,
        enforced_tool_output_policy=None,
    )

    async def skip_memory(*args, **kwargs):
        return None

    monkeypatch.setattr(agent, "_add_message_to_memory", skip_memory)
    context = _build_context("task-progressive-unmanaged-tools")
    message = Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        headers={"context": context},
    )

    await agent.async_policy(
        SimpleNamespace(observer="user", from_agent_name=None, context=None),
        message=message,
    )

    actual = captured["tools"] or []
    assert [schema["function"]["name"] for schema in actual] == [
        "run_code",
        "browser_navigate",
        "browser_snapshot",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capture_method",
    ["_record_llm_call_request", "_update_llm_call_observability"],
)
async def test_async_policy_request_or_assembly_capture_failure_does_not_skip_model(
    monkeypatch,
    capture_method,
):
    model_calls = 0

    class CapturingAgent(Agent):
        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": "hello"}]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal model_calls
            model_calls += 1
            return ModelResponse(
                id="resp-capture-begin",
                model="fake-model",
                content="done",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )

    agent = CapturingAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="custom",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    def fail_request_capture(*args, **kwargs):
        raise RuntimeError("agent-request-capture-secret")

    monkeypatch.setattr(agent, capture_method, fail_request_capture)

    async def skip_memory(*args, **kwargs):
        return None

    monkeypatch.setattr(agent, "_add_message_to_memory", skip_memory)
    context = _build_context("task-agent-request-fail-open")
    message = Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        headers={"context": context},
    )

    await agent.async_policy(
        SimpleNamespace(observer="user", from_agent_name=None, context=None),
        message=message,
    )

    assert model_calls == 1


@pytest.mark.asyncio
async def test_async_policy_response_capture_failure_preserves_success(monkeypatch):
    class SuccessfulAgent(Agent):
        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": "hello"}]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            return ModelResponse(
                id="resp-capture-finish",
                model="fake-model",
                content="done",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )

    agent = SuccessfulAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="custom",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    def fail_response_capture(*args, **kwargs):
        raise RuntimeError("agent-response-capture-secret")

    monkeypatch.setattr(agent, "_record_llm_call_response", fail_response_capture)

    async def skip_memory(*args, **kwargs):
        return None

    monkeypatch.setattr(agent, "_add_message_to_memory", skip_memory)
    context = _build_context("task-agent-response-success")
    message = Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        headers={"context": context},
    )

    result = await agent.async_policy(
        SimpleNamespace(observer="user", from_agent_name=None, context=None),
        message=message,
    )

    assert len(result) == 1
    assert result[0].policy_info == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("primary", [ValueError("provider-primary"), asyncio.CancelledError()])
async def test_async_policy_response_capture_failure_preserves_primary_error(
    monkeypatch,
    primary,
):
    class FailingAgent(Agent):
        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": "hello"}]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            raise primary

    agent = FailingAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="custom",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    def fail_response_capture(*args, **kwargs):
        raise RuntimeError("agent-response-capture-secret")

    monkeypatch.setattr(agent, "_record_llm_call_response", fail_response_capture)
    context = _build_context("task-agent-response-primary")
    message = Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        headers={"context": context},
    )

    if isinstance(primary, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await agent.async_policy(
                SimpleNamespace(observer="user", from_agent_name=None, context=None),
                message=message,
            )
    else:
        with pytest.raises(AWorldRuntimeException, match="provider-primary"):
            await agent.async_policy(
                SimpleNamespace(observer="user", from_agent_name=None, context=None),
                message=message,
            )


@pytest.mark.asyncio
async def test_async_policy_raises_cancelled_error_when_context_is_interrupted_and_llm_returns_none():
    class InterruptedAgent(Agent):
        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "hello"},
            ]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            return None

    agent = InterruptedAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )
    context = _build_context()

    async def interrupted_status():
        return TaskStatusValue.INTERRUPTED

    context.get_task_status = interrupted_status
    message = Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        headers={"context": context},
    )

    with pytest.raises(asyncio.CancelledError):
        await agent.async_policy(
            SimpleNamespace(observer="user", from_agent_name=None, context=None),
            message=message,
        )


def test_context_cache_effective_enablement_defaults_to_true_without_amni_context():
    agent = _build_agent()
    context = _build_context()

    assert agent._is_context_cache_enabled(context) is True
    assert agent._allow_provider_native_cache(context) is True


def test_provider_native_cache_requested_defaults_on_for_anthropic_when_allowed():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="anthropic",
            llm_model_name="claude-3-5-sonnet-20241022",
            llm_api_key="fake-key",
        ),
    )

    assert agent._provider_native_cache_requested(_build_context(), "anthropic", {}) is True


def test_context_cache_effective_enablement_respects_agent_and_model_opt_out():
    class FakeContext:
        def get_agent_context_config(self, namespace):
            return AgentContextConfig(
                context_cache=ContextCacheConfig(enabled=False, allow_provider_native_cache=True)
            )

    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_config=ModelConfig(
                llm_provider="openai",
                llm_model_name="fake-model",
                llm_api_key="fake-key",
                context_cache=ContextCacheConfig(enabled=False, allow_provider_native_cache=True),
            )
        ),
    )

    assert agent._is_context_cache_enabled(FakeContext()) is False
    assert agent._allow_provider_native_cache(FakeContext()) is False
