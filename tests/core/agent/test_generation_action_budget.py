from __future__ import annotations

import asyncio

import pytest

import aworld.agents.llm_agent as llm_agent_module
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.context.base import Context
from aworld.core.context.generation_budget import (
    GenerationBudgetExceeded,
    GenerationBudgetPolicy,
    GenerationStopReason,
)
from aworld.core.context.session import Session
from aworld.core.event.base import Constants, Message
from aworld.core.task import Task
from aworld.models.model_response import Function, ModelResponse, ToolCall


class _ToolAgent(Agent):
    async def _filter_tools(self, context=None):
        return [
            {
                "type": "function",
                "function": {
                    "name": "workspace__write",
                    "description": "write a workspace artifact",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]


class _NoToolAgent(Agent):
    async def _filter_tools(self, context=None):
        return None


def _agent(
    *,
    policy: GenerationBudgetPolicy,
    with_tools: bool = True,
    attempts: int = 1,
) -> Agent:
    cls = _ToolAgent if with_tools else _NoToolAgent
    agent = cls(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
        generation_budget_policy=policy,
        llm_max_attempts=attempts,
        llm_retry_delay=0,
    )
    # Provider helpers are monkeypatched below. Avoid constructing an SDK client.
    agent._llm = object()
    return agent


def _message(task_id: str = "generation-budget") -> Message:
    context = Context(task_id=task_id, session=Session(session_id=f"{task_id}-s"))
    context.set_task(Task(id=task_id, name=task_id, input="test request"))
    return Message(
        category=Constants.AGENT,
        sender="user",
        receiver="Aworld",
        headers={"context": context},
    )


@pytest.fixture(autouse=True)
def _silence_event_delivery(monkeypatch: pytest.MonkeyPatch):
    async def noop_send_message(*args, **kwargs):
        return None

    monkeypatch.setattr(llm_agent_module, "send_message", noop_send_message)


@pytest.mark.asyncio
async def test_active_tool_free_stream_is_cut_off_and_repaired_once(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[list[dict]] = []
    primary_closed = asyncio.Event()

    async def fake_stream(*args, **kwargs):
        call_messages = kwargs["messages"]
        calls.append(call_messages)
        if len(calls) == 1:
            try:
                while True:
                    await asyncio.sleep(0.002)
                    yield ModelResponse(
                        id="primary",
                        model="fake-model",
                        content="planning ",
                    )
            finally:
                primary_closed.set()
            return
        yield ModelResponse(
            id="repair",
            model="fake-model",
            content="",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    function=Function(name="workspace__write", arguments="{}"),
                )
            ],
        )

    monkeypatch.setattr(llm_agent_module, "acall_llm_model_stream", fake_stream)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.1,
            active_tool_free_timeout_seconds=0.02,
            action_repair_timeout_seconds=0.1,
            action_repair_max_output_tokens=128,
            partial_response_context_chars=64,
        )
    )
    message = _message("active-stream")

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "create the artifact"}],
        message=message,
        stream=True,
    )

    assert primary_closed.is_set()
    assert len(calls) == 2
    assert response.tool_calls[0].function.name == "workspace__write"
    assert calls[1][-2]["role"] == "assistant"
    assert "planning" in calls[1][-2]["content"]
    assert len(calls[1][-2]["content"]) <= 64
    assert calls[1][-1]["role"] == "user"
    assert "next concrete action" in calls[1][-1]["content"]
    events = message.context.context_info["generation_budget_events"]
    assert [event["reason"] for event in events] == [
        GenerationStopReason.ACTIVE_STREAM_OVER_BUDGET.value
    ]
    assert events[0]["repair_scheduled"] is True
    assert events[0]["repair_attempted"] is True
    assert events[0]["partial_response_available"] is True
    assert (
        message.context.context_info["post_tool_progress_metrics"][
            "generation_action_repair_scheduled_count"
        ]
        == 1
    )


@pytest.mark.asyncio
async def test_stream_idle_timeout_is_typed_and_keeps_partial_response(
    monkeypatch: pytest.MonkeyPatch,
):
    async def idle_stream(*args, **kwargs):
        yield ModelResponse(id="idle", model="fake-model", content="partial")
        await asyncio.Event().wait()

    monkeypatch.setattr(llm_agent_module, "acall_llm_model_stream", idle_stream)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.01,
            active_tool_free_timeout_seconds=0.2,
            action_repair_timeout_seconds=0.1,
        ),
        attempts=1,
    )
    message = _message("idle-stream")

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "work"}],
            message=message,
            stream=True,
        )

    assert raised.value.reason is GenerationStopReason.IDLE_TIMEOUT
    assert raised.value.partial_response.content == "partial"
    event = message.context.context_info["generation_budget_events"][-1]
    assert event["reason"] == GenerationStopReason.IDLE_TIMEOUT.value
    assert event["partial_response_chars"] == len("partial")
    assert event["partial_response_available"] is True


@pytest.mark.asyncio
async def test_no_tool_stream_is_not_subject_to_active_action_deadline(
    monkeypatch: pytest.MonkeyPatch,
):
    async def text_only_stream(*args, **kwargs):
        for _ in range(8):
            await asyncio.sleep(0.003)
            yield ModelResponse(id="text", model="fake-model", content="answer ")

    monkeypatch.setattr(
        llm_agent_module, "acall_llm_model_stream", text_only_stream
    )
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.1,
            active_tool_free_timeout_seconds=0.005,
            action_repair_timeout_seconds=0.1,
        ),
        with_tools=False,
    )
    message = _message("no-tool-stream")

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "write a long answer"}],
        message=message,
        stream=True,
    )

    assert response.content == "answer " * 8
    assert message.context.context_info.get("generation_budget_events") is None


@pytest.mark.asyncio
async def test_started_tool_call_disarms_tool_free_deadline(
    monkeypatch: pytest.MonkeyPatch,
):
    async def tool_stream(*args, **kwargs):
        yield ModelResponse(
            id="tool",
            model="fake-model",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    function=Function(
                        name="workspace__write", arguments='{"content":"'
                    ),
                )
            ],
        )
        for _ in range(6):
            await asyncio.sleep(0.003)
            yield ModelResponse(
                id="tool",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        function=Function(name="unknown", arguments="x"),
                    )
                ],
            )

    monkeypatch.setattr(llm_agent_module, "acall_llm_model_stream", tool_stream)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.1,
            active_tool_free_timeout_seconds=0.005,
            action_repair_timeout_seconds=0.1,
        )
    )
    message = _message("tool-started")

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "write"}],
        message=message,
        stream=True,
    )

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].function.arguments.endswith("xxxxxx")
    assert message.context.context_info.get("generation_budget_events") is None


@pytest.mark.asyncio
async def test_all_optional_deadlines_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    async def immediate_provider(*args, **kwargs):
        return ModelResponse(id="ok", model="fake-model", content="ok")

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", immediate_provider)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=None,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )
    message = _message("deadlines-disabled")

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "hello"}],
        message=message,
        stream=False,
    )

    assert response.content == "ok"


@pytest.mark.asyncio
async def test_provider_timeout_is_not_mislabeled_as_framework_deadline(
    monkeypatch: pytest.MonkeyPatch,
):
    class APITimeoutError(Exception):
        pass

    async def provider_timeout(*args, **kwargs):
        raise APITimeoutError("provider read timeout")

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", provider_timeout)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=0.1,
        ),
        with_tools=False,
        attempts=1,
    )
    message = _message("provider-timeout")

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "hello"}],
            message=message,
            stream=False,
        )

    assert raised.value.reason is GenerationStopReason.PROVIDER_TIMEOUT
    assert message.context.context_info["generation_budget_events"][-1][
        "reason"
    ] == GenerationStopReason.PROVIDER_TIMEOUT.value


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_and_is_recorded_separately(
    monkeypatch: pytest.MonkeyPatch,
):
    provider_cancelled = asyncio.Event()
    provider_started = asyncio.Event()

    async def blocked_provider(*args, **kwargs):
        provider_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            provider_cancelled.set()
            raise

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", blocked_provider)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=0.1,
        ),
        with_tools=False,
    )
    message = _message("caller-cancel")
    task = asyncio.create_task(
        agent.invoke_model(
            messages=[{"role": "user", "content": "hello"}],
            message=message,
            stream=False,
        )
    )
    await asyncio.wait_for(provider_started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider_cancelled.is_set()
    events = message.context.context_info["generation_budget_events"]
    assert events[-1]["reason"] == GenerationStopReason.CALLER_CANCELLED.value


@pytest.mark.asyncio
async def test_action_repair_cannot_repair_itself_in_a_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = 0

    async def endless_active_stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        while True:
            await asyncio.sleep(0.002)
            yield ModelResponse(
                id=f"response-{calls}", model="fake-model", content="more analysis"
            )

    monkeypatch.setattr(
        llm_agent_module, "acall_llm_model_stream", endless_active_stream
    )
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.1,
            active_tool_free_timeout_seconds=0.01,
            action_repair_timeout_seconds=0.015,
            action_repair_max_output_tokens=64,
        )
    )
    message = _message("repair-once")

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "create the artifact"}],
            message=message,
            stream=True,
        )

    assert raised.value.reason is GenerationStopReason.ACTION_REPAIR_TIMEOUT
    assert calls == 2
    events = message.context.context_info["generation_budget_events"]
    assert [event["reason"] for event in events] == [
        GenerationStopReason.ACTIVE_STREAM_OVER_BUDGET.value,
        GenerationStopReason.ACTION_REPAIR_TIMEOUT.value,
    ]
