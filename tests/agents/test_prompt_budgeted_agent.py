from __future__ import annotations

from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import Agent
from aworld.agents.prompt_budgeted_agent import PromptBudgetedAgent
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.context.amni.prompt.assembly import DefaultPromptAssemblyProvider
from aworld.core.context.amni.prompt.assembly.budget import (
    BudgetedPromptAssemblyProvider,
    PromptBudgetExceededError,
    PromptBudgetPolicy,
)


def _config(
    *,
    max_input_tokens: int = 1_000,
    max_model_len: int = 500,
    params: dict | None = None,
) -> AgentConfig:
    return AgentConfig(
        llm_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="unused",
            max_model_len=max_model_len,
            params=params or {},
        ),
        max_input_tokens=max_input_tokens,
    )


def test_prompt_budget_agent_resolves_model_aware_input_budget() -> None:
    agent = PromptBudgetedAgent(
        name="budgeted",
        conf=_config(max_input_tokens=450, max_model_len=500),
        prompt_budget_policy=PromptBudgetPolicy(reserved_output_tokens=100),
        tool_names=[],
    )
    request_kwargs: dict = {}

    metadata = agent._build_prompt_assembly_metadata(request_kwargs=request_kwargs)

    assert metadata["prompt_budget"]["reserved_output_tokens"] == 100
    assert metadata["prompt_budget"]["input_budget"] == 400
    assert request_kwargs == {"max_tokens": 100}


def test_output_limit_is_resolved_once_for_budget_and_provider_request() -> None:
    agent = PromptBudgetedAgent(
        name="budgeted",
        conf=_config(params={"max_tokens": 120, "unrelated": "kept"}),
        prompt_budget_policy=PromptBudgetPolicy(reserved_output_tokens=200),
        tool_names=[],
    )
    request_kwargs = {"max_completion_tokens": 80}

    metadata = agent._build_prompt_assembly_metadata(request_kwargs=request_kwargs)

    assert metadata["prompt_budget"]["reserved_output_tokens"] == 80
    assert request_kwargs == {"max_completion_tokens": 80}
    assert agent.conf.llm_config.params == {"unrelated": "kept"}


def test_prompt_budget_agent_wraps_normal_context_provider() -> None:
    delegate = DefaultPromptAssemblyProvider()
    context = SimpleNamespace(get_prompt_assembly_provider=lambda agent: delegate)
    agent = PromptBudgetedAgent(
        name="budgeted",
        conf=_config(),
        prompt_budget_policy=PromptBudgetPolicy(reserved_output_tokens=100),
        tool_names=[],
    )

    provider = agent._get_prompt_assembly_provider(context)

    assert isinstance(provider, BudgetedPromptAssemblyProvider)
    assert provider.delegate is delegate


def test_base_agent_does_not_opt_in_to_prompt_budgeting() -> None:
    agent = Agent(name="base", conf=_config(), tool_names=[])

    provider = agent._get_prompt_assembly_provider()
    metadata = agent._build_prompt_assembly_metadata(request_kwargs={})

    assert isinstance(provider, DefaultPromptAssemblyProvider)
    assert not isinstance(provider, BudgetedPromptAssemblyProvider)
    assert "prompt_budget" not in metadata


@pytest.mark.asyncio
async def test_final_budget_assertion_stops_provider_call(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_called = False

    async def fake_base_invoke(self, messages, message=None, **kwargs):
        nonlocal provider_called
        provider_called = True
        return object()

    monkeypatch.setattr(Agent, "invoke_model", fake_base_invoke)
    agent = PromptBudgetedAgent(
        name="budgeted",
        conf=_config(max_input_tokens=20, max_model_len=100),
        prompt_budget_policy=PromptBudgetPolicy(reserved_output_tokens=20),
        tool_names=[],
    )

    with pytest.raises(PromptBudgetExceededError):
        await agent.invoke_model(
            [{"role": "user", "content": "required input " * 100}],
            message=SimpleNamespace(context=None),
        )

    assert provider_called is False


@pytest.mark.asyncio
async def test_final_budget_assertion_forwards_resolved_output_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_base_invoke(self, messages, message=None, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(Agent, "invoke_model", fake_base_invoke)
    agent = PromptBudgetedAgent(
        name="budgeted",
        conf=_config(max_input_tokens=1_000, max_model_len=500),
        prompt_budget_policy=PromptBudgetPolicy(reserved_output_tokens=100),
        tool_names=[],
    )

    result = await agent.invoke_model(
        [{"role": "user", "content": "small input"}],
        message=SimpleNamespace(context=None),
    )

    assert result == "ok"
    assert captured["max_tokens"] == 100

