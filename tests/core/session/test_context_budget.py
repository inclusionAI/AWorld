"""Exercise compaction through the real loop and fenced Session ownership."""

import asyncio
from dataclasses import replace

import pytest

from aworld.cli.trajectory import build_trajectory
from aworld.core.agent import Agent
from aworld.core.agent.messages import AssistantMessage, ModelRequest, ToolCall, ToolResultMessage, UserMessage
from aworld.core.agent.usage import ModelResponseError, TokenUsage
from aworld.core.context import BudgetPolicy, Context, ContextBudget
from aworld.core.context.budget import estimate_request, measure_request, request_anchor
from aworld.core.context.simple import ContextEntry
from aworld.core.session import RunStatus, create_session
from aworld.core.tool import Tool
from aworld.core.tool.function import ToolSchema


BUDGET = ContextBudget(context_window=6000, output_reserve=800, safety_margin=100,
                       trigger_ratio=0.7, keep_recent_tokens=1500, summary_max_tokens=200)


class LongModel:
    context_identity = "fixture-route"

    def __init__(self, *, turns=10, summary=None):
        self.turns, self.summary = turns, summary
        self.requests, self.summary_requests = [], []

    async def complete(self, request):
        if not request.tools:
            self.summary_requests.append(request)
            if self.summary is not None:
                return await self.summary(request)
            return AssistantMessage("Goal: inspect files. Verified earlier results; continue the remaining work.",
                                    usage=TokenUsage(estimate_request(request), 20))
        self.requests.append(request)
        n = len(self.requests)
        usage = TokenUsage(estimate_request(request), 30)
        if n > self.turns:
            return AssistantMessage("done", usage=usage)
        return AssistantMessage(tool_calls=(ToolCall(f"inspect-{n}", "inspect", {}),), usage=usage)


async def make_session(model, *, budget=BUDGET, output_size=1800, **kwargs):
    async def inspect(arguments, execution):
        return "verified result " + "x" * output_size
    agent = Agent(model=model, tools=[Tool("inspect", "Inspect files", {"type": "object"}, inspect)],
                  system_prompt="Preserve workspace rules.", max_turns=20, **kwargs)
    session = await create_session(agent=agent, context=Context(policy=BudgetPolicy(budget)))
    return agent, session


def assert_balanced(request):
    pending = set()
    for message in request.messages:
        if isinstance(message, AssistantMessage):
            assert not pending
            pending.update(call.id for call in message.tool_calls)
        elif isinstance(message, ToolResultMessage):
            assert message.tool_call_id in pending
            pending.remove(message.tool_call_id)
        else:
            assert not pending
    assert not pending


def test_long_loop_repeated_compaction_preserves_canonical_history_and_bills_summaries():
    async def run():
        model = LongModel()
        agent, session = await make_session(model)
        try:
            handle = await session.submit("Inspect files and preserve the results")
            result = await handle.result()
            assert result.status == RunStatus.COMPLETED, result.error
            history = await session.history()
            checkpoints = [entry for entry in history if entry.kind == "context.compaction"]
            assert len(checkpoints) >= 2
            assert len(model.summary_requests) == len(checkpoints)
            assert len([entry for entry in history if entry.kind == "tool.result"]) == 10
            assert len([entry for entry in history if entry.kind == "assistant"]) == 11
            assert all(checkpoint.data["after"]["tokens"] < checkpoint.data["before"]["estimated_tokens"] * .9
                       for checkpoint in checkpoints)
            assert checkpoints[1].data["source_start_index"] == checkpoints[0].data["first_kept_index"]
            assert "<context-summary>" in model.summary_requests[1].messages[0].content
            assert "verified result" in model.summary_requests[1].messages[0].content
            assert all(request.max_output_tokens == 800 for request in model.requests)
            assert all(request.max_output_tokens == 200 for request in model.summary_requests)
            for request in model.requests:
                assert_balanced(request)
                assert request.messages[0].content == "Inspect files and preserve the results"
                assert request.system_prompt == "Preserve workspace rules."
            assert "<context-summary>" in str(model.requests[-1].messages)
            trajectory = build_trajectory(history, result=result, agent=agent)
            totals = trajectory["extra"]["run_metrics"]
            assert totals["model_calls"] == len(model.requests) + len(model.summary_requests)
            assert totals["input_tokens"] == sum(estimate_request(request) for request in model.requests + model.summary_requests)
            assert totals["output_tokens"] == 30 * len(model.requests) + 20 * len(model.summary_requests)
            assert trajectory["extra"]["compaction"]["completed"] == len(checkpoints)
            assert any(event.type == "context.compaction.finished" for event in [item async for item in handle.events()])
            prior_usage = totals["input_tokens"]
            follow_up = await (await session.submit("Continue inspecting")).result()
            assert follow_up.status == RunStatus.COMPLETED
            follow_trajectory = build_trajectory(await session.history(), result=follow_up, agent=agent)
            assert follow_trajectory["final_metrics"]["total_prompt_tokens"] == prior_usage + follow_trajectory["extra"]["run_metrics"]["input_tokens"]
            assert any(message.content == "Continue inspecting" for message in model.requests[-1].messages)
        finally:
            await session.close()
    asyncio.run(run())


def test_below_threshold_does_not_summarize_and_sessions_do_not_share_checkpoints():
    async def run():
        model = LongModel(turns=1)
        _, first = await make_session(model, output_size=10)
        _, second = await make_session(model, output_size=10)
        try:
            for session in (first, second):
                assert (await (await session.submit("small")).result()).status == RunStatus.COMPLETED
                assert not any(entry.kind.startswith("context.compaction") for entry in await session.history())
            assert not model.summary_requests
            assert model.requests[-1].messages == (UserMessage("small"),)
        finally:
            await first.close()
            await second.close()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["blank", "tool", "unchanged", "error", "timeout", "length"])
def test_failed_summary_keeps_previous_view_and_stops_futile_calls(failure):
    async def summarize(request):
        if failure == "error":
            raise RuntimeError("fixture outage")
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "length":
            raise ModelResponseError("truncated summary", usage=TokenUsage(120, 200))
        return AssistantMessage("" if failure in ("blank", "tool") else request.messages[0].content,
                                (ToolCall("never-execute", "inspect", {}),) if failure == "tool" else (),
                                usage=TokenUsage(120, 20))
    async def run():
        model = LongModel(turns=6, summary=summarize)
        agent, session = await make_session(model, budget=replace(BUDGET, summary_timeout=.01))
        try:
            result = await (await session.submit("inspect files")).result()
            assert result.status == RunStatus.COMPLETED, result.error
            history = await session.history()
            assert not any(entry.kind == "context.compaction" for entry in history)
            assert len(model.summary_requests) == 1
            assert len([entry for entry in history if entry.kind == "tool.result"]) == 6
            assert len([message for message in model.requests[-1].messages if isinstance(message, ToolResultMessage)]) == 6
            assert not any(call.id == "never-execute" for message in model.requests[-1].messages
                           if isinstance(message, AssistantMessage) for call in message.tool_calls)
            usage = build_trajectory(history, result=result, agent=agent)["extra"]["run_metrics"]
            assert usage["model_calls"] == 8
            if failure not in ("error", "timeout"):
                assert usage["output_tokens"] == (410 if failure == "length" else 230)
            else:
                assert usage["output_tokens"] is None
            if failure in ("error", "timeout"):
                assert usage["status"] == "partial"
        finally:
            await session.close()
    asyncio.run(run())


def test_oversized_system_and_irreducible_tool_unit_fail_before_another_model_request():
    async def run():
        class Model:
            async def complete(self, request):
                pytest.fail("Model should not be called for an oversized request")
        agent = Agent(model=Model(), tools=[], system_prompt="s" * 18000)
        session = await create_session(agent=agent, context=Context(policy=BudgetPolicy(BUDGET)))
        try:
            result = await (await session.submit("hello")).result()
            assert result.status == RunStatus.FAILED
            assert "context_capacity_exceeded" in result.error.message
        finally:
            await session.close()
        model = LongModel()
        _, session = await make_session(model, output_size=18000)
        try:
            result = await (await session.submit("inspect")).result()
            assert result.status == RunStatus.FAILED
            assert len(model.requests) == 1
            assert not model.summary_requests
            assert "context_capacity_exceeded" in result.error.message
        finally:
            await session.close()
    asyncio.run(run())


def test_usage_anchor_requires_matching_route_envelope_and_prefix():
    model = LongModel()
    request = ModelRequest("rules", (UserMessage("goal"),), (), 800)
    entry = ContextEntry("run", "assistant", {"usage": {"input_tokens": 1000}, "context_anchor": request_anchor(request, model)})
    current = replace(request, messages=(*request.messages, AssistantMessage("answer"), UserMessage("next")))
    measured = measure_request(current, (entry,), model)
    assert measured["method"] == "provider_input_plus_estimated_delta"
    assert measured["tokens"] > 1000
    for changed in (replace(current, system_prompt="different"), replace(current, max_output_tokens=700),
                    replace(current, messages=(UserMessage("edited goal"),)),
                    replace(current, tools=(ToolSchema("extra", "Tool", {"type": "object"}),))):
        assert measure_request(changed, (entry,), model)["method"] == "estimated"
    model.context_identity = "new route"
    assert measure_request(current, (entry,), model)["method"] == "estimated"
    assert estimate_request(replace(request, tools=(ToolSchema("large", "x" * 1000, {}),))) > estimate_request(request)


def test_summary_attempts_are_bounded_and_tool_ids_survive_compacted_followup():
    async def run():
        async def broken(request):
            raise RuntimeError("outage")
        model = LongModel(summary=broken)
        _, session = await make_session(model)
        try:
            result = await (await session.submit("inspect")).result()
            assert result.status == RunStatus.FAILED
            assert "context_capacity_exceeded" in result.error.message
            assert len(model.summary_requests) == BUDGET.max_attempts
        finally:
            await session.close()
        model = LongModel()
        _, session = await make_session(model)
        try:
            assert (await (await session.submit("inspect")).result()).status == RunStatus.COMPLETED
            model.turns = 20
            model.requests.clear()  # The next run attempts an ID now hidden by compaction.
            result = await (await session.submit("continue")).result()
            assert result.status == RunStatus.FAILED
            assert "Tool call IDs must be unique" in result.error.message
        finally:
            await session.close()
    asyncio.run(run())


def test_cancelled_summary_cannot_write_a_checkpoint_even_if_model_ignores_cancel():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        async def summarize(request):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                return AssistantMessage("late summary")
            return AssistantMessage("summary")
        model = LongModel(summary=summarize)
        _, session = await make_session(model)
        try:
            handle = await session.submit("inspect")
            await asyncio.wait_for(started.wait(), 2)
            await handle.cancel()
            assert (await handle.result()).status == RunStatus.CANCELLED
            assert not any(entry.kind == "context.compaction" for entry in await session.history())
        finally:
            release.set()
            await session.close()
    asyncio.run(run())


@pytest.mark.parametrize("configured", [300, 145.5])
def test_summary_wait_uses_configured_timeout_and_remaining_run_deadline(monkeypatch, configured):
    import aworld.core.context.budget as module
    from aworld.core.session import RunOptions
    original_wait_for = asyncio.wait_for
    waits = []

    async def observe_wait(awaitable, timeout):
        waits.append(timeout)
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(module.asyncio, "wait_for", observe_wait)

    async def run():
        model = LongModel(turns=6)
        _, session = await make_session(model, budget=replace(BUDGET, summary_timeout=configured))
        try:
            assert (await (await session.submit("inspect")).result()).status == RunStatus.COMPLETED
            assert model.summary_requests
            assert configured in waits
        finally:
            await session.close()
        waits.clear()
        started = asyncio.Event()

        async def pending_summary(request):
            started.set()
            await asyncio.Event().wait()

        model = LongModel(summary=pending_summary)
        _, session = await make_session(model, budget=replace(BUDGET, summary_timeout=configured))
        try:
            result = await (await session.submit("inspect", options=RunOptions(timeout_seconds=.2))).result()
            assert started.is_set()
            assert result.status != RunStatus.COMPLETED
            assert any(0 < timeout <= .2 for timeout in waits if timeout is not None)
            assert not any(e.kind == "context.compaction" for e in await session.history())
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.parametrize("changes", [{"context_window": 0}, {"output_reserve": True}, {"trigger_ratio": 1},
                                    {"summary_timeout": float("nan")}, {"keep_recent_tokens": 5000}])
def test_invalid_budgets_rejected(changes):
    with pytest.raises(ValueError):
        replace(BUDGET, **changes)
