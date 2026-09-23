from __future__ import annotations

from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import Agent
from aworld.agents.prompt_budgeted_agent import PromptBudgetedAgent
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.models.context_window import DEFAULT_CONTEXT_WINDOW_TOKENS
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
            context_compiler={"provider_protocol_reserve": 0, "safety_margin_tokens": 0},
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


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_input_cap", [None, 128000])
async def test_registered_large_window_has_no_implicit_128k_input_clamp(monkeypatch, explicit_input_cap):
    called = False
    async def invoke(self, messages, message=None, **kwargs):
        nonlocal called
        called = True
        return kwargs
    monkeypatch.setattr(Agent, "invoke_model", invoke)
    monkeypatch.setattr(BudgetedPromptAssemblyProvider, "estimate_request_tokens",
                        lambda **kwargs: {"total": 200000, "tool_tokens": 0})
    agent = PromptBudgetedAgent(
        name="large-window",
        conf=AgentConfig(llm_config=ModelConfig(llm_model_name="gpt-4.1", llm_api_key="unused"),
                         max_input_tokens=explicit_input_cap),
        prompt_budget_policy=PromptBudgetPolicy(reserved_output_tokens=32768),
        tool_names=[],
    )
    if explicit_input_cap is None:
        result = await agent.invoke_model([{"role":"user", "content":"estimated request"}])
        assert called and result["max_tokens"] == 32768
        assert agent._resolve_input_budget(32768) == 1_047_576 - 32768 - 256 - 512
    else:
        with pytest.raises(PromptBudgetExceededError):
            await agent.invoke_model([{"role":"user", "content":"estimated request"}])
        assert not called


def _attach_offline_model(agent, *, candidate_policy=None, http=False):
    from aworld.models.llm import LLMModel
    from aworld.models.openai_provider import OpenAIProvider

    provider = object.__new__(OpenAIProvider)
    provider.model_name = agent.conf.llm_config.llm_model_name
    provider.kwargs = {"params": dict(agent.conf.llm_config.params or {})}
    provider.is_http_provider = http
    agent._llm = LLMModel(
        conf=agent.conf.llm_config, custom_provider=provider,
        context_candidate_policy=candidate_policy,
    )
    return agent._llm


def _routed_agent(*, params=None, max_model_len=None):
    return PromptBudgetedAgent(
        name="routed-budget",
        conf=AgentConfig(llm_config=ModelConfig(
            llm_provider="openai", llm_model_name="gpt-4.1", max_model_len=max_model_len, params=params or {},
        )),
        prompt_budget_policy=PromptBudgetPolicy(reserved_output_tokens=1024),
        tool_names=[],
    )


def test_metadata_does_not_create_client_or_borrow_unresolved_route_capacity(monkeypatch):
    def unexpected_client(self):
        raise AssertionError("metadata must not construct a model client")

    monkeypatch.setattr(Agent, "llm", property(unexpected_client))
    agent = _routed_agent(params={"model": "gpt-4"}, max_model_len=1_000_000)
    budget = agent._build_prompt_assembly_metadata()["prompt_budget"]
    assert agent._llm is None
    assert budget["context_limit"] == DEFAULT_CONTEXT_WINDOW_TOKENS
    assert budget["context_window_source"] == "fallback"
    assert budget["model_name"] == "unknown-model"


def test_prompt_metadata_uses_actual_configured_and_per_call_model_routes():
    agent = _routed_agent(params={"model": "gpt-4"})
    _attach_offline_model(agent)
    small = agent._build_prompt_assembly_metadata()["prompt_budget"]
    assert small["model_name"] == "gpt-4"
    assert small["input_budget"] == 8192 - 1024 - 256 - 512
    request = {"model_name": "gpt-4", "model": "gpt-4.1"}
    large = agent._build_prompt_assembly_metadata(request_kwargs=request)["prompt_budget"]
    assert large["model_name"] == "gpt-4.1"
    assert large["input_budget"] == 1_047_576 - 1024 - 256 - 512
    assert request == {"model_name": "gpt-4", "model": "gpt-4.1", "max_tokens": 1024}


@pytest.mark.parametrize("http,expected_reserve", [(False, 200000), (True, 1024)])
def test_prompt_metadata_reserves_final_sdk_output_and_respects_http_nesting(http, expected_reserve):
    agent = _routed_agent(params={"extra_body": {"max_tokens": 200000}})
    _attach_offline_model(agent, http=http)
    budget = agent._build_prompt_assembly_metadata()["prompt_budget"]
    assert budget["reserved_output_tokens"] == expected_reserve
    assert budget["input_budget"] == 1_047_576 - expected_reserve - 256 - 512


def test_prompt_budget_uses_custom_compiler_capacity_and_reserve_floor():
    from aworld.core.context.compiler import CandidateCompilePolicy, ContextInputBudget, FinalCompilePolicy

    policy = CandidateCompilePolicy(final_policy=FinalCompilePolicy(
        compiler_version="test", policy_version="test", input_budget=ContextInputBudget(
        context_limit=15000, reserved_output_tokens=6000,
        provider_protocol_reserve=20, safety_margin_tokens=30,
    )))
    agent = _routed_agent()
    _attach_offline_model(agent, candidate_policy=policy)
    budget = agent._build_prompt_assembly_metadata()["prompt_budget"]
    assert budget["context_limit"] == 15000
    assert budget["reserved_output_tokens"] == 6000
    assert budget["input_budget"] == 8950


@pytest.mark.parametrize("wire_model", [None, ""])
def test_unknown_wire_identity_does_not_reuse_declared_deployment_window(wire_model):
    agent = _routed_agent(params={"extra_body": {"model": wire_model}}, max_model_len=1_000_000)
    _attach_offline_model(agent)
    budget = agent._build_prompt_assembly_metadata()["prompt_budget"]
    assert budget["context_limit"] == DEFAULT_CONTEXT_WINDOW_TOKENS
    assert budget["context_window_source"] == "fallback"
    assert budget["model_name"] == "unknown-model"


@pytest.mark.asyncio
async def test_final_preflight_uses_wire_model_and_output_capacity_without_provider_call(monkeypatch):
    called = []
    estimated_models = []

    async def unexpected_invoke(self, messages, message=None, **kwargs):
        called.append(kwargs)

    def estimate(**kwargs):
        estimated_models.append(kwargs["model_name"])
        return {"total": 9000, "tool_tokens": 0}

    monkeypatch.setattr(Agent, "invoke_model", unexpected_invoke)
    monkeypatch.setattr(BudgetedPromptAssemblyProvider, "estimate_request_tokens", estimate)
    agent = _routed_agent(params={"model": "gpt-4"})
    _attach_offline_model(agent)
    with pytest.raises(PromptBudgetExceededError) as failure:
        await agent.invoke_model([{"role": "user", "content": "estimated input"}])
    assert not called
    assert estimated_models == ["gpt-4"]
    assert failure.value.input_budget == 8192 - 1024 - 256 - 512


@pytest.mark.parametrize("source,expected_reserve", [
    ("typed_default", 1024), ("typed_explicit_default", 4096),
    ("typed_mutated", 6000), ("dict", 4096),
])
def test_real_agent_config_bridge_preserves_output_reserve_provenance(source, expected_reserve):
    config = AgentConfig(llm_config=ModelConfig(llm_provider="openai", llm_model_name="gpt-4"))
    config.llm_config.params["unrelated"] = "in-place-setting"
    if source == "typed_explicit_default":
        config.llm_config.context_compiler.reserved_output_tokens = 4096
    elif source == "typed_mutated":
        config.llm_config.context_compiler.reserved_output_tokens = 6000
    original = config.model_dump() if source == "dict" else config
    agent = Agent(name="reserve-provenance", conf=original, tool_names=[])
    assert agent.conf.llm_config.params["unrelated"] == "in-place-setting"
    assert ("reserved_output_tokens" in agent.conf.llm_config.context_compiler) is (source != "typed_default")
    model = _attach_offline_model(agent)
    budget = model.resolve_request_context_budget({"max_tokens": 1024})
    assert budget.reserved_output_tokens == expected_reserve
    assert budget.available_input_tokens == 8192 - expected_reserve - 256 - 512


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("params,call_kwargs,expected_model", [
    ({"model": "gpt-4"}, {}, "gpt-4"),
    ({"model": "gpt-4"}, {"model": "gpt-4.1"}, "gpt-4.1"),
    ({"model": "gpt-4"}, {"model_name": "gpt-4.1"}, "gpt-4"),
    ({}, {"model_name": "gpt-4"}, "gpt-4"),
    ({"extra_body": {"model": "gpt-4"}}, {"model": "gpt-4.1"}, "gpt-4"),
])
async def test_real_agent_transport_matches_prompt_preflight_routing(monkeypatch, stream, params, call_kwargs, expected_model):
    import aworld.agents.llm_agent as agent_module
    from aworld.core.context.base import Context
    from aworld.core.event.base import Constants, Message
    from aworld.models.model_response import ModelResponse
    from aworld.models.request_model import effective_request_model_name

    agent = _routed_agent(params=params)
    model = _attach_offline_model(agent)
    captured = []
    estimates = []

    def response(llm, kwargs):
        # Mock only the transport facade; the real Agent invocation, routing,
        # streaming consumption and PromptBudget preflight all execute.
        captured.append((effective_request_model_name(llm.provider, kwargs),
                         llm.resolve_request_context_budget(kwargs), kwargs))
        return ModelResponse(id="offline", model=captured[-1][0], content="done", finish_reason="stop")

    async def completion(llm, **kwargs):
        return response(llm, kwargs)

    async def stream_completion(llm, **kwargs):
        yield response(llm, kwargs)

    async def ignore_message(*args, **kwargs):
        return None

    def estimate(**kwargs):
        estimates.append(kwargs["model_name"])
        return {"total": 1000, "tool_tokens": 0}

    monkeypatch.setattr(agent_module, "acall_llm_model", completion)
    monkeypatch.setattr(agent_module, "acall_llm_model_stream", stream_completion)
    monkeypatch.setattr(agent_module, "send_message", ignore_message)
    monkeypatch.setattr(agent, "_log_messages", lambda *args, **kwargs: None)
    monkeypatch.setattr(BudgetedPromptAssemblyProvider, "estimate_request_tokens", estimate)
    metadata = agent._build_prompt_assembly_metadata(request_kwargs=dict(call_kwargs))["prompt_budget"]
    context = Context(task_id="prompt-route-parity", session_id="prompt-route-session")
    result = await agent.invoke_model(
        [{"role": "user", "content": "go"}],
        message=Message(category=Constants.AGENT, headers={"context": context}),
        prepared_tools=[], stream=stream, **call_kwargs,
    )
    assert result.content == "done"
    assert len(captured) == 1
    actual_model, actual_budget, transport_kwargs = captured[0]
    assert actual_model == expected_model == metadata["model_name"] == estimates[-1]
    assert actual_budget.available_input_tokens == metadata["input_budget"]
    assert actual_budget.reserved_output_tokens == metadata["reserved_output_tokens"]
    for key, value in call_kwargs.items():
        assert transport_kwargs[key] == value
    if "model" not in call_kwargs:
        assert "model" not in transport_kwargs
