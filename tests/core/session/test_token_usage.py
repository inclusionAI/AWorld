"""Provider receipts survive execution, failure and session reuse without double counting."""

import asyncio
from types import SimpleNamespace

import pytest

from aworld.cli.trajectory import build_trajectory
from aworld.core.agent import Agent
from aworld.core.agent.messages import AssistantMessage, ModelRequest, UserMessage
from aworld.core.agent.usage import ModelResponseError, TokenUsage
from aworld.core.session import create_session
from aworld.models.chat_completions import ProviderModel, parse_response, request_payload
from aworld.models.token_accounting import parse_usage


def response(usage=None, reason="stop"):
    return {"usage": usage, "choices": [{"finish_reason": reason,
            "message": {"role": "assistant", "content": "answer"}}]}


@pytest.mark.parametrize("raw,expected", [
    ({"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
      "prompt_tokens_details": {"cached_tokens": 80},
      "completion_tokens_details": {"reasoning_tokens": 12}}, (100, 20, 80, None, 12, 120)),
    ({"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 80}, (100, 20, 80, None, None, 120)),
    ({"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 80,
      "cache_creation_input_tokens": 10}, (100, 20, 80, 10, None, 120)),
    ({"prompt_tokens": 0, "completion_tokens": 0}, (0, 0, None, None, None, 0)),
    ({"prompt_tokens": 10}, (10, None, None, None, None, None)),
    ({}, (None, None, None, None, None, None)),
])
def test_provider_field_semantics(raw, expected):
    usage = parse_usage(raw)
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
            usage.cache_write_tokens, usage.reasoning_tokens, usage.total_tokens) == expected


@pytest.mark.parametrize("value", [True, -1, 1.5, "7"])
def test_invalid_usage_does_not_fail_a_valid_response_or_fabricate_zero(value):
    message = parse_response(response({"prompt_tokens": value, "completion_tokens": 2}))
    assert message.content == "answer"
    assert message.usage.input_tokens is None
    assert message.usage.output_tokens == 2
    assert "prompt_tokens" in message.usage.invalid_fields


def test_conflicting_aliases_and_impossible_subsets_remain_visible():
    usage = parse_usage({"prompt_tokens": 10, "input_tokens": 20, "completion_tokens": 2,
                         "completion_tokens_details": {"reasoning_tokens": 3}})
    assert usage.input_tokens is None and usage.reasoning_tokens is None
    assert set(usage.invalid_fields) == {"prompt_tokens", "input_tokens", "reasoning_tokens"}
    assert parse_usage({"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 999}).total_tokens == 12


def test_usage_is_not_sent_back_to_the_model():
    value = request_payload(ModelRequest("", (AssistantMessage("prior", usage=TokenUsage(100, 20)),), ()), "test", None)
    assert value["messages"][0] == {"role": "assistant", "content": "prior"}


def test_provider_prefers_original_receipt_and_ignores_synthetic_defaults():
    async def check(receipt, expected):
        class Provider:
            async def acompletion(self, **kwargs):
                return receipt
        answer = await ProviderModel(Provider(), model="test").complete(ModelRequest("", (UserMessage("hello"),), ()))
        assert answer.usage == expected
    asyncio.run(check(SimpleNamespace(raw_response=response(), usage={"prompt_tokens": 0,
                     "completion_tokens": 0}, raw_usage=None, usage_reported=False), None))
    asyncio.run(check(SimpleNamespace(raw_response=response(), raw_usage={"prompt_tokens": 7,
                     "completion_tokens": 3}, usage_reported=True), TokenUsage(7, 3)))


def test_session_aggregate_and_current_run_are_separate_and_missing_counts_stay_null():
    async def run():
        class Model:
            async def complete(self, request):
                return AssistantMessage("answer", usage=TokenUsage(100, 20, 80)
                                        if len(request.messages) == 1 else TokenUsage(200, 30, 150))
        agent = Agent(model=Model(), tools=[])
        session = await create_session(agent=agent)
        try:
            first = await (await session.submit("first")).result()
            second = await (await session.submit("second")).result()
            trajectory = build_trajectory(await session.history(), result=second, agent=agent)
            assert trajectory["final_metrics"]["total_prompt_tokens"] == 300
            assert trajectory["final_metrics"]["total_completion_tokens"] == 50
            assert trajectory["final_metrics"]["total_cached_tokens"] == 230
            metrics = trajectory["extra"]["run_metrics"]
            assert metrics["run_id"] == second.run_id != first.run_id
            assert (metrics["input_tokens"], metrics["output_tokens"], metrics["total_tokens"]) == (200, 30, 230)
            assert metrics["cache_write_tokens"] is None and metrics["status"] == "reported"
            assert [s["metrics"]["prompt_tokens"] for s in trajectory["steps"] if s["source"] == "agent"] == [100, 200]
        finally:
            await session.close()
    asyncio.run(run())


@pytest.mark.parametrize("second_usage,expected_input,status", [(None, None, "partial"), (TokenUsage(0, 0), 100, "reported")])
def test_partial_coverage_differs_from_reported_zero(second_usage, expected_input, status):
    async def run():
        class Model:
            async def complete(self, request):
                return AssistantMessage("answer", usage=TokenUsage(100, 20) if len(request.messages) == 1 else second_usage)
        agent = Agent(model=Model(), tools=[])
        session = await create_session(agent=agent)
        try:
            await (await session.submit("first")).result()
            result = await (await session.submit("second")).result()
            t = build_trajectory(await session.history(), result=result, agent=agent)
            assert t["final_metrics"]["total_prompt_tokens"] == expected_input
            assert t["final_metrics"]["extra"]["status"] == status
            assert t["final_metrics"]["extra"]["reported_subtotals"]["input_tokens"] == 100
        finally:
            await session.close()
    asyncio.run(run())


def test_length_rejection_keeps_billable_usage_without_executing_tools():
    with pytest.raises(ModelResponseError) as error:
        parse_response(response({"prompt_tokens": 100, "completion_tokens": 20}, "length"))
    assert error.value.usage == TokenUsage(100, 20)
    async def run():
        class Model:
            async def complete(self, request):
                return parse_response(response({"prompt_tokens": 100, "completion_tokens": 20}, "length"))
        agent = Agent(model=Model(), tools=[])
        session = await create_session(agent=agent)
        try:
            result = await (await session.submit("first")).result()
            assert result.status.value == "failed"
            t = build_trajectory(await session.history(), result=result, agent=agent)
            assert t["extra"]["run_metrics"]["total_tokens"] == 120
            assert t["steps"][-1]["metrics"]["completion_tokens"] == 20
            assert not any(step.get("tool_calls") for step in t["steps"])
        finally:
            await session.close()
    asyncio.run(run())
