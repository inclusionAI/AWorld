from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import aworld.agents.llm_agent as module
from aworld.agents.llm_agent import LlmOutputParser
from aworld.core.context.execution_state import get_execution_state
from aworld.core.context.generation_budget import GenerationBudgetPolicy
from aworld.models.model_response import Function, ModelResponse, ToolCall
from tests.core.agent.test_generation_action_budget import _agent, _message


@pytest.fixture(autouse=True)
def silence_events(monkeypatch):
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(module, "send_message", noop)


def tool(arguments, call_id="call-1"):
    return ToolCall(id=call_id, function=Function(name="workspace__write", arguments=arguments))


@pytest.mark.asyncio
async def test_stream_length_response_is_recovered_before_tool_execution(monkeypatch):
    calls = []
    async def stream(*args, **kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            yield ModelResponse(id="cut", model="fake", content="I will write ",
                                tool_calls=[tool('{"path":"out.txt"}')])
            yield ModelResponse(id="cut", model="fake", finish_reason="length")
        else:
            yield ModelResponse(id="complete", model="fake", tool_calls=[tool('{"path":"out.txt","text":"done"}')])
            yield ModelResponse(id="complete", model="fake", finish_reason="tool_calls")
    monkeypatch.setattr(module, "acall_llm_model_stream", stream)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=2)
    message = _message("length-recovery")
    result = await agent.invoke_model([{"role": "user", "content": "write"}], message=message, stream=True)
    assert len(calls) == 2
    assert result.finish_reason == "tool_calls"
    assert json.loads(result.tool_calls[0].function.arguments)["text"] == "done"
    assert "No tool calls" in calls[1][-1]["content"]
    assert get_execution_state(message.context)["status"] == "running"


@pytest.mark.asyncio
async def test_required_stream_terminal_reason_retries_implicit_eof(monkeypatch):
    calls = 0

    async def stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield ModelResponse(id="cut", model="fake", content="I will write")
            return
        yield ModelResponse(
            id="complete",
            model="fake",
            tool_calls=[tool('{"path":"out.txt","text":"done"}')],
        )
        yield ModelResponse(
            id="complete", model="fake", finish_reason="tool_calls"
        )

    monkeypatch.setenv("AWORLD_REQUIRE_STREAM_FINISH_REASON", "true")
    monkeypatch.setattr(module, "acall_llm_model_stream", stream)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=2)
    message = _message("implicit-eof")

    result = await agent.invoke_model(
        [{"role": "user", "content": "write"}], message=message, stream=True
    )

    assert calls == 2
    assert result.finish_reason == "tool_calls"
    assert json.loads(result.tool_calls[0].function.arguments)["text"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad,reason", [
    (ModelResponse(id="x", model="f", content="Unfinished plan", finish_reason="length"), "model_output_truncated"),
    (ModelResponse(id="x", model="f", reasoning_content="Need more work", finish_reason="stop"), "reasoning_only_response"),
    (ModelResponse(id="x", model="f", tool_calls=[tool('{"path":')], finish_reason="tool_calls"), "incomplete_tool_arguments"),
    (ModelResponse(id="x", model="f", tool_calls=[tool('{}'), tool('[]', 'call-2')]), "invalid_tool_arguments"),
])
async def test_unusable_response_exhausts_bounded_recovery_truthfully(monkeypatch, bad, reason):
    calls = 0
    async def response(*args, **kwargs):
        nonlocal calls
        calls += 1
        return bad
    monkeypatch.setattr(module, "acall_llm_model", response)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=2)
    message = _message(reason)
    result = await agent.invoke_model([{"role": "user", "content": "work"}], message=message)
    assert calls == 2
    assert result.tool_calls == []
    assert result.message["aworld_incomplete_reason"] == reason
    assert get_execution_state(message.context)["status"] == "incomplete"
    assert get_execution_state(message.context)["recoverable"] is False


@pytest.mark.asyncio
async def test_deployed_probe_composition_never_finishes_reasoning_length_response(monkeypatch):
    # Same three boundaries as the deployed AST probe, now with real classes.
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    agent.context = _message("probe").context
    async def stream(*args, **kwargs):
        yield ModelResponse(id="probe", model="fake", reasoning_content="Need to implement before finishing.")
        yield ModelResponse(id="probe", model="fake", finish_reason="length")
    monkeypatch.setattr(module, "acall_llm_model_stream", stream)
    from aworld.core.context.generation_budget import GenerationBudgetController
    message = _message("probe")
    agent.context = message.context
    response = await agent._consume_model_stream(messages=[], message=message, tools=[],
        float_temperature=0.1, prompt_tokens_est=0,
        controller=GenerationBudgetController(GenerationBudgetPolicy(total_timeout_seconds=5)), request_kwargs={})
    assert response.finish_reason == "length"
    parsed = await LlmOutputParser().parse(response, agent_id=agent.id())
    assert not agent.is_agent_finished(response, parsed)
    assert get_execution_state(agent.context)["status"] == "incomplete"


@pytest.mark.asyncio
async def test_loop_summary_cannot_turn_budget_stop_into_success():
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    message = _message("budget")
    message.context.context_info[f"agent_loop_budget_finalized:{agent.id()}"] = True
    await agent._resolve_completion_at_loop_budget(message)
    state = get_execution_state(message.context)
    assert state["status"] == "budget_exhausted"
    assert state["recoverable"] is True


@pytest.mark.asyncio
async def test_validation_repair_exhaustion_is_incomplete():
    from aworld.core.tool.base import Observation
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    message = _message("validation")
    agent._collect_result_validation_evidence = lambda context: {}
    message.context.context_info[agent._result_validation_retry_key(agent.id())] = 1
    await agent._retry_for_result_validation(validation_feedback="required artifact missing",
        observation=Observation(observer=agent.id(), content=""), info={}, message=message, kwargs={})
    assert get_execution_state(message.context)["status"] == "incomplete"
