import pytest

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.context.amni.config import AgentContextConfig, ContextCacheConfig
from aworld.core.context.amni.prompt.assembly import PromptAssemblyPlan
from aworld.core.context.base import Context
from aworld.core.context.context_state import ContextState
from aworld.core.event.base import Constants, Message
from aworld.core.task import Task
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
