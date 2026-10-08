from __future__ import annotations

import json

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


@pytest.mark.parametrize(
    "content",
    (
        "I need to see the rest of sim.c first. Let me read the rest of the file.",
        "The download is at 86%. Let me wait for it to complete.",
        "Next, I will run the focused tests.",
        "I am going to continue with the implementation",
    ),
)
def test_final_response_wording_does_not_override_model_completion(content):
    response = ModelResponse(
        id="future-work", model="fake", content=content, finish_reason="stop"
    )

    assert (
        module.LLMAgent._incomplete_model_response_reason(response)
        is None
    )


@pytest.mark.parametrize(
    "content",
    (
        "Implemented the change and all focused tests pass.",
        "The next step for an operator is deployment.",
        "Everything is complete. Let me know if you need anything else.",
        "The phrase `Let me read the file` is an example of unfinished work.",
        "```text\nLet me read the file.\n```\nThe analysis is complete.",
    ),
)
def test_completion_detector_does_not_reject_reports_or_conversational_closers(content):
    response = ModelResponse(
        id="complete", model="fake", content=content, finish_reason="stop"
    )

    assert module.LLMAgent._incomplete_model_response_reason(response) is None


@pytest.mark.asyncio
async def test_model_stop_does_not_trigger_a_wording_based_retry(monkeypatch):
    calls = []

    async def response(*args, **kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            return ModelResponse(
                id="unfinished",
                model="fake",
                content="The download is at 86%. Let me wait for it to complete.",
                finish_reason="stop",
            )
        return ModelResponse(
            id="complete",
            model="fake",
            content="The download completed and the requested artifacts were generated.",
            finish_reason="stop",
        )

    monkeypatch.setattr(module, "acall_llm_model", response)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=2)
    message = _message("future-work-recovery")

    result = await agent.invoke_model(
        [{"role": "user", "content": "finish the download"}],
        message=message,
        stream=False,
    )

    assert len(calls) == 1
    assert result.content.startswith("The download is at 86%")


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
    assert calls[1][-2]["role"] == "assistant"
    assert "I will write" in calls[1][-2]["content"]
    assert "not a final answer or executable action" in calls[1][-2]["content"]
    assert "No tool calls" in calls[1][-1]["content"]
    assert get_execution_state(message.context)["status"] == "running"


@pytest.mark.asyncio
async def test_provider_resolution_uses_request_start_blocker_watermark(monkeypatch):
    calls = 0
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=1)
    message = _message("provider-watermark")

    async def response(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            from aworld.core.context.execution_state import record_execution_state

            # Simulate a newer concurrent provider boundary becoming incomplete
            # while this older request is still in flight.
            record_execution_state(
                message.context,
                agent.id(),
                "incomplete",
                "model_output_truncated",
            )
        return ModelResponse(
            id=f"complete-{calls}",
            model="fake",
            tool_calls=[tool('{"path":"out.txt","text":"done"}', f"call-{calls}")],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(module, "acall_llm_model", response)

    await agent.invoke_model(
        [{"role": "user", "content": "write"}], message=message, stream=False
    )
    assert get_execution_state(message.context)["status"] == "incomplete"

    await agent.invoke_model(
        [{"role": "user", "content": "continue"}], message=message, stream=False
    )
    assert get_execution_state(message.context)["status"] == "running"


@pytest.mark.asyncio
async def test_internal_control_provider_action_does_not_resolve_solver_blocker(
    monkeypatch,
):
    from aworld.core.context.execution_state import record_execution_state

    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=1)
    message = _message("internal-control-watermark")
    record_execution_state(
        message.context,
        agent.id(),
        "incomplete",
        "model_output_truncated",
    )

    async def response(*args, **kwargs):
        return ModelResponse(
            id="internal-control",
            model="fake",
            tool_calls=[tool('{"path":"decision.json","text":"continue"}')],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(module, "acall_llm_model", response)
    await agent.invoke_model(
        [{"role": "user", "content": "internal checkpoint"}],
        message=message,
        stream=False,
        _execution_state_resolution_mode="control",
    )

    state = get_execution_state(message.context)
    assert state["status"] == "incomplete"
    assert state["reason"] == "model_output_truncated"


@pytest.mark.asyncio
async def test_whitespace_only_provider_response_is_incomplete(monkeypatch):
    async def response(*args, **kwargs):
        return ModelResponse(
            id="whitespace",
            model="fake",
            content="  \n\t  ",
            finish_reason="stop",
        )

    monkeypatch.setattr(module, "acall_llm_model", response)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=1)
    message = _message("whitespace-response")

    result = await agent.invoke_model(
        [{"role": "user", "content": "finish"}], message=message, stream=False
    )

    assert result.message["aworld_incomplete_reason"] == "empty_model_response"
    assert get_execution_state(message.context)["status"] == "incomplete"
    agent.context = message.context
    parsed = await LlmOutputParser().parse(result, agent_id=agent.id())
    assert agent.is_agent_finished(result, parsed) is False


def test_contract_resolution_uses_pre_assessment_watermark() -> None:
    from aworld.core.agent.base import AgentResult
    from aworld.core.common import ActionModel
    from aworld.core.context.compiler import CompletionContract, CompletionMode
    from aworld.core.context.execution_state import record_execution_state

    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=1)
    context = _message("assessment-watermark").context
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    original_assessment = context.assess_completion_contract

    def assess(**kwargs):
        record_execution_state(
            context,
            agent.id(),
            "incomplete",
            "completion_contract_unsatisfied",
        )
        return original_assessment(**kwargs)

    context.assess_completion_contract = assess
    agent.context = context
    response = ModelResponse(
        id="complete",
        model="fake",
        content="done",
        finish_reason="stop",
    )
    parsed = AgentResult(
        actions=[ActionModel(agent_name=agent.id(), policy_info="done")],
        current_state=None,
        is_call_tool=False,
    )

    assert agent.is_agent_finished(response, parsed) is True
    state = get_execution_state(context)
    assert state["status"] == "incomplete"
    assert state["reason"] == "completion_contract_unsatisfied"


@pytest.mark.asyncio
async def test_reasoning_only_recovery_retains_a_bounded_working_tail(monkeypatch):
    calls = []
    reasoning = "discarded-prefix-" + "R" * 9000

    async def response(*args, **kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            return ModelResponse(
                id="reasoning",
                model="fake",
                reasoning_content=reasoning,
                finish_reason="stop",
            )
        return ModelResponse(
            id="complete",
            model="fake",
            tool_calls=[tool('{"path":"out.txt","text":"done"}')],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(module, "acall_llm_model", response)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=2)
    message = _message("reasoning-tail-recovery")

    result = await agent.invoke_model(
        [{"role": "user", "content": "write"}],
        message=message,
        stream=False,
    )

    retained = calls[1][-2]
    assert retained["role"] == "assistant"
    assert "discarded-prefix" not in retained["content"]
    assert retained["content"].endswith("R" * 128)
    assert len(retained["content"]) < len(reasoning)
    assert "Return one complete, minimal Tool call" in calls[1][-1]["content"]
    assert result.finish_reason == "tool_calls"


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
    (ModelResponse(id="x", model="f", reasoning_content="Need more work", finish_reason="length"), "reasoning_only_response"),
    (ModelResponse(id="x", model="f", tool_calls=[tool('{"path":')], finish_reason="tool_calls"), "incomplete_tool_arguments"),
    (ModelResponse(id="x", model="f", tool_calls=[tool('{}'), tool('[]', 'call-2')]), "invalid_tool_arguments"),
])
async def test_unusable_response_exhausts_bounded_recovery_truthfully(monkeypatch, bad, reason):
    calls = 0
    provider_calls = []
    async def response(*args, **kwargs):
        nonlocal calls
        calls += 1
        provider_calls.append(kwargs)
        return bad
    monkeypatch.setattr(module, "acall_llm_model", response)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=3)
    message = _message(reason)
    result = await agent.invoke_model([{"role": "user", "content": "work"}], message=message)
    # One bounded action-projection continuation is enough for this logical
    # turn. Higher provider retry settings do not repeat the same long analysis;
    # the outer task loop retains authority to continue with a fresh Tool turn.
    assert calls == 2
    assert "tool_choice" not in provider_calls[0]
    assert provider_calls[1]["tool_choice"] == "required"
    assert result.tool_calls == []
    assert result.message["aworld_incomplete_reason"] == reason
    assert get_execution_state(message.context)["status"] == "incomplete"
    assert get_execution_state(message.context)["recoverable"] is True
    agent.context = message.context
    parsed = await LlmOutputParser().parse(result, agent_id=agent.id())
    assert not agent.is_agent_finished(result, parsed)


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
async def test_stream_chunks_do_not_emit_agent_info_logs(monkeypatch):
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    message = _message("bounded stream log")
    secret = "private-stream-payload"

    async def stream(*args, **kwargs):
        yield ModelResponse(
            id="bounded-log",
            model="fake",
            content=secret,
            reasoning_content=secret * 2,
            finish_reason="stop",
        )

    log_messages = []
    monkeypatch.setattr(module, "acall_llm_model_stream", stream)
    monkeypatch.setattr(module.logger, "info", log_messages.append)

    response = await agent._consume_model_stream(
        messages=[],
        message=message,
        tools=[],
        float_temperature=0.1,
        prompt_tokens_est=0,
        controller=module.GenerationBudgetController(
            GenerationBudgetPolicy(total_timeout_seconds=5)
        ),
        request_kwargs={},
    )

    assert response.content == secret
    assert response.reasoning_content == secret * 2
    assert log_messages == []


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
