"""Provider reasoning stays on its route, survives tools, and consumes context budget."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aworld.cli.trajectory import build_trajectory
from aworld.core.agent import Agent
from aworld.core.agent.messages import AssistantMessage, ModelRequest, ToolCall, UserMessage, messages_from_history
from aworld.core.agent.usage import ModelResponseError, TokenUsage
from aworld.core.context import BudgetPolicy, Context, ContextBudget
from aworld.core.context.budget import estimate_request, measure_request, request_anchor
from aworld.core.context.simple import ContextEntry
from aworld.core.session import InMemorySessionStore, create_session
from aworld.core.tool import Tool
from aworld.core.tool.function import ToolSchema
from aworld.core.tool.sessions import session_tools
from aworld.models.chat_completions import ProviderModel, parse_response, request_payload


SECRET = "private provider continuation state"
SCHEMA = ToolSchema("inspect", "Inspect", {"type": "object"})


def raw(content="answer", *, reasoning=SECRET, call=None, reason="stop"):
    message = {"role": "assistant", "content": content, "reasoning_content": reasoning}
    if call:
        message["tool_calls"] = [{"id": call, "type": "function", "function": {
            "name": "inspect", "arguments": "{}"}}]
        reason = "tool_calls"
    return {"choices": [{"finish_reason": reason, "message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_parse_preserves_reasoning_without_exposing_it_as_content_or_repr():
    message = parse_response(raw(None, call="a"))
    assert message.content == "" and message.reasoning_content == SECRET
    assert message.usage == TokenUsage(10, 5)
    assert SECRET not in repr(message)
    request = ModelRequest("", (message,), (SCHEMA,))
    assert request_payload(request, "dsv41")["messages"][0]["reasoning_content"] == SECRET
    assert "reasoning_content" not in request_payload(replace(request, tools=()), "dsv41")["messages"][0]
    assert "reasoning_content" not in request_payload(request, "dsv41", "none")["messages"][0]


@pytest.mark.parametrize("reasoning", [0, {}, []])
def test_malformed_reasoning_is_rejected_with_billable_usage(reasoning):
    with pytest.raises(ModelResponseError) as error:
        parse_response(raw(reasoning=reasoning))
    assert error.value.usage == TokenUsage(10, 5)
    assert error.value.code == "invalid_response"


def test_reasoning_only_is_an_observed_failure_never_a_final_answer():
    with pytest.raises(ModelResponseError) as error:
        parse_response(raw(None))
    assert error.value.code == "empty_final_response"
    assert error.value.diagnostics["response_kind"] == "reasoning_only"
    assert SECRET not in str(error.value) + str(error.value.diagnostics)


def test_cross_route_replay_and_empty_history_compatibility():
    async def check():
        class Provider:
            base_url = "https://route-one.invalid/v1"
            def __init__(self): self.calls = []
            async def acompletion(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(raw_response=raw())
        provider = Provider()
        source = ProviderModel(provider, model="dsv41")
        response = await source.complete(ModelRequest("", (UserMessage("task"),), (SCHEMA,)))
        request = ModelRequest("", (response, AssistantMessage("old history")), (SCHEMA,))
        await source.complete(request)
        assert [m["reasoning_content"] for m in provider.calls[-1]["messages"]] == [SECRET, ""]
        other_model = ProviderModel(provider, model="other")
        await other_model.complete(request)
        assert all("reasoning_content" not in m for m in provider.calls[-1]["messages"])
        provider.base_url = "https://route-two.invalid/v1"
        await source.complete(request)
        assert provider.calls[-1]["messages"][0]["reasoning_content"] == ""
        entry = ContextEntry("r", "assistant", {"content": response.content, "tool_calls": [],
            "reasoning_content": SECRET, "reasoning_identity": response.reasoning_identity})
        assert messages_from_history((entry,), reasoning_identity=source.reasoning_identity)[0].reasoning_content is None
    asyncio.run(check())


def test_replayed_reasoning_is_budgeted_and_anchor_changes_are_detected():
    model = SimpleNamespace(context_identity="route")
    message = AssistantMessage("answer", reasoning_content="x" * 9000)
    request = ModelRequest("rules", (UserMessage("task"), message), (SCHEMA,), 1000)
    without = replace(request, messages=(UserMessage("task"), AssistantMessage("answer")))
    assert estimate_request(request) >= estimate_request(without) + 3000
    assert estimate_request(replace(request, tools=())) == estimate_request(replace(without, tools=()))
    entry = ContextEntry("r", "assistant", {"usage": {"input_tokens": 4000},
        "context_anchor": request_anchor(request, model)})
    assert measure_request(request, (entry,), model)["method"] == "provider_input_plus_estimated_delta"
    assert measure_request(without, (entry,), model)["method"] == "estimated"


def test_reasoning_survives_session_turns_but_is_hidden_from_cross_session_tools_and_atif():
    async def check():
        class Provider:
            base_url = "https://same-route.invalid/v1"
            def __init__(self): self.calls = []
            async def acompletion(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(raw_response=raw())
        provider = Provider()
        model = ProviderModel(provider, model="dsv41")
        store = InMemorySessionStore()
        async def inspect(arguments, context): return "observed"
        agent = Agent(model=model, tools=[Tool("inspect", "Inspect", {}, inspect)])
        session = await create_session(agent=agent, store=store)
        try:
            await (await session.submit("first")).result()
            result = await (await session.submit("second")).result()
            assert provider.calls[-1]["messages"][1]["reasoning_content"] == SECRET
            trajectory = build_trajectory(await session.history(), result=result, agent=agent)
            assert SECRET not in str(trajectory)
            assert trajectory["extra"]["run_metrics"]["total_tokens"] == 15
            tools = {tool.name: tool for tool in session_tools(store)}
            page = await tools["read_session"].execute({"session_id": session.id}, None)
            search = await tools["search_sessions"].execute({"query": SECRET}, None)
            assert SECRET not in str(page) and search["total_matches"] == 0
            other = await create_session(agent=agent, store=store)
            try:
                await (await other.submit("unrelated task")).result()
                assert all("reasoning_content" not in m for m in provider.calls[-1]["messages"])
            finally: await other.close()
        finally: await session.close()
    asyncio.run(check())


def test_compaction_counts_reasoning_retains_tail_and_excludes_it_from_summary_source():
    async def check():
        class Model:
            reasoning_identity = "fixture"
            context_identity = "fixture"
            def __init__(self): self.requests, self.summaries = [], []
            async def complete(self, request):
                if not request.tools:
                    self.summaries.append(request)
                    assert SECRET not in str(request)
                    return AssistantMessage("Earlier inspections verified. Continue remaining work.")
                self.requests.append(request)
                n = len(self.requests)
                if n > 10: return AssistantMessage("done")
                return AssistantMessage(tool_calls=(ToolCall(f"inspect-{n}", "inspect", {}),),
                    reasoning_content=SECRET + "x" * 1500, reasoning_identity=self.reasoning_identity)
        model = Model()
        async def inspect(arguments, context): return "verified"
        budget = ContextBudget(context_window=6000, output_reserve=800, safety_margin=100,
            trigger_ratio=.7, keep_recent_tokens=1500, summary_max_tokens=200)
        agent = Agent(model=model, tools=[Tool("inspect", "Inspect", {}, inspect)])
        session = await create_session(agent=agent, context=Context(policy=BudgetPolicy(budget)))
        try:
            result = await (await session.submit("inspect ten times")).result()
            assert result.status.value == "completed", result.error
            assert model.summaries
            assert any(m.reasoning_content for m in model.requests[-1].messages if isinstance(m, AssistantMessage))
            assert SECRET not in str(build_trajectory(await session.history(), result=result, agent=agent))
        finally: await session.close()
    asyncio.run(check())
