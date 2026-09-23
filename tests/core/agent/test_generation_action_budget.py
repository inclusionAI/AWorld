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


def test_default_agent_uses_max_steps_without_generation_deadlines() -> None:
    agent = _ToolAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    policy = agent._resolve_generation_budget_policy()

    assert policy.total_timeout_seconds is None
    assert policy.stream_idle_timeout_seconds is None
    assert policy.active_tool_free_timeout_seconds is None
    assert policy.action_repair_timeout_seconds is None
    assert policy.action_repair_enabled is False


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

        yield ModelResponse(
            id="tool", model="fake-model", finish_reason="tool_calls",
            tool_calls=[ToolCall(id="call-1", function=Function(name="unknown", arguments='"}'))],
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
    assert response.tool_calls[0].function.arguments == '{"content":"xxxxxx"}'
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
async def test_generation_deadline_does_not_wait_forever_for_provider_cleanup(
    monkeypatch: pytest.MonkeyPatch,
):
    provider_started = asyncio.Event()
    provider_release = asyncio.Event()
    provider_tasks: list[asyncio.Task] = []

    async def stubborn_provider(*args, **kwargs):
        provider_tasks.append(asyncio.current_task())
        provider_started.set()
        while not provider_release.is_set():
            try:
                await provider_release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", stubborn_provider)
    monkeypatch.setattr(
        llm_agent_module, "_GENERATION_CLEANUP_GRACE_SECONDS", 0.01
    )
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.01,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
        attempts=1,
    )

    try:
        with pytest.raises(GenerationBudgetExceeded) as raised:
            await asyncio.wait_for(
                agent.invoke_model(
                    messages=[{"role": "user", "content": "hello"}],
                    message=_message("stubborn-provider"),
                    stream=False,
                ),
                timeout=0.15,
            )
        assert raised.value.reason is GenerationStopReason.CALL_DEADLINE_EXCEEDED
        assert provider_started.is_set()
    finally:
        provider_release.set()
        if provider_tasks:
            await asyncio.wait_for(provider_tasks[0], timeout=1)


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_blocked_by_provider_cleanup(
    monkeypatch: pytest.MonkeyPatch,
):
    provider_started = asyncio.Event()
    provider_release = asyncio.Event()
    provider_tasks: list[asyncio.Task] = []

    async def stubborn_provider(*args, **kwargs):
        provider_tasks.append(asyncio.current_task())
        provider_started.set()
        while not provider_release.is_set():
            try:
                await provider_release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", stubborn_provider)
    monkeypatch.setattr(
        llm_agent_module, "_GENERATION_CLEANUP_GRACE_SECONDS", 0.01
    )
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )
    task = asyncio.create_task(
        agent.invoke_model(
            messages=[{"role": "user", "content": "hello"}],
            message=_message("stubborn-provider-caller-cancel"),
            stream=False,
        )
    )
    await asyncio.wait_for(provider_started.wait(), timeout=1)

    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.15)
    finally:
        provider_release.set()
        if provider_tasks:
            await asyncio.wait_for(provider_tasks[0], timeout=1)


@pytest.mark.asyncio
async def test_stream_close_is_bounded_when_provider_cleanup_stalls(
    monkeypatch: pytest.MonkeyPatch,
):
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    close_tasks: list[asyncio.Task] = []

    class StubbornStream:
        emitted = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.emitted:
                self.emitted = True
                return ModelResponse(
                    id="complete", model="fake-model", content="complete"
                )
            raise StopAsyncIteration

        async def aclose(self):
            close_tasks.append(asyncio.current_task())
            close_started.set()
            while not close_release.is_set():
                try:
                    await close_release.wait()
                except asyncio.CancelledError:
                    continue

    monkeypatch.setattr(
        llm_agent_module,
        "acall_llm_model_stream",
        lambda *args, **kwargs: StubbornStream(),
    )
    monkeypatch.setattr(
        llm_agent_module, "_GENERATION_CLEANUP_GRACE_SECONDS", 0.01
    )
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )

    try:
        response = await asyncio.wait_for(
            agent.invoke_model(
                messages=[{"role": "user", "content": "hello"}],
                message=_message("stubborn-stream-close"),
                stream=True,
            ),
            timeout=0.15,
        )
        assert response.content == "complete"
        assert close_started.is_set()
    finally:
        close_release.set()
        if close_tasks:
            await asyncio.wait_for(close_tasks[0], timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("close_behavior", ("raise", "non_awaitable"))
async def test_broken_stream_close_does_not_replace_completed_response(
    monkeypatch: pytest.MonkeyPatch, close_behavior: str
):
    class BrokenCloseStream:
        emitted = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.emitted:
                raise StopAsyncIteration
            self.emitted = True
            return ModelResponse(id="ok", model="fake-model", content="complete")

        def aclose(self):
            if close_behavior == "raise":
                raise RuntimeError("broken stream cleanup")
            return None

    monkeypatch.setattr(
        llm_agent_module,
        "acall_llm_model_stream",
        lambda *args, **kwargs: BrokenCloseStream(),
    )
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "hello"}],
        message=_message(f"broken-close-{close_behavior}"),
        stream=True,
    )

    assert response.content == "complete"


@pytest.mark.asyncio
async def test_broken_stream_close_does_not_replace_primary_error(
    monkeypatch: pytest.MonkeyPatch,
):
    class BrokenCloseStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("primary provider failure")

        def aclose(self):
            raise RuntimeError("secondary cleanup failure")

    monkeypatch.setattr(
        llm_agent_module,
        "acall_llm_model_stream",
        lambda *args, **kwargs: BrokenCloseStream(),
    )
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
        attempts=1,
    )

    with pytest.raises(Exception, match="primary provider failure") as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "hello"}],
            message=_message("broken-close-primary-error"),
            stream=True,
        )

    assert "secondary cleanup failure" not in str(raised.value)


@pytest.mark.asyncio
async def test_detached_provider_cleanup_capacity_is_finite(
    monkeypatch: pytest.MonkeyPatch,
):
    release = asyncio.Event()
    detached: list[asyncio.Task] = []

    async def stubborn_provider():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setenv("AWORLD_MAX_PENDING_GENERATION_TASKS", "3")
    monkeypatch.setattr(
        llm_agent_module, "_GENERATION_CLEANUP_GRACE_SECONDS", 0.001
    )
    llm_agent_module._DETACHED_GENERATION_TASKS.clear()
    llm_agent_module._ACTIVE_GENERATION_TASKS.clear()
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

    try:
        for _ in range(3):
            task = agent._create_generation_task(stubborn_provider())
            assert task is not None
            detached.append(task)
            await asyncio.sleep(0)
            await agent._cancel_generation_task(task)
        assert len(llm_agent_module._DETACHED_GENERATION_TASKS) == 3

        with pytest.raises(
            Exception, match="cleanup capacity is exhausted"
        ):
            await agent._await_generation_operation(
                stubborn_provider(),
                controller=llm_agent_module.GenerationBudgetController(
                    agent._resolve_generation_budget_policy()
                ),
                streaming=False,
            )
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*detached), timeout=1)
        await asyncio.sleep(0)
        llm_agent_module._DETACHED_GENERATION_TASKS.clear()
        llm_agent_module._ACTIVE_GENERATION_TASKS.clear()


@pytest.mark.asyncio
async def test_concurrent_provider_timeouts_cannot_exceed_cleanup_capacity(
    monkeypatch: pytest.MonkeyPatch,
):
    release = asyncio.Event()
    provider_tasks: list[asyncio.Task] = []

    async def stubborn_provider():
        provider_tasks.append(asyncio.current_task())
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setenv("AWORLD_MAX_PENDING_GENERATION_TASKS", "3")
    monkeypatch.setattr(
        llm_agent_module, "_GENERATION_CLEANUP_GRACE_SECONDS", 0.001
    )
    llm_agent_module._DETACHED_GENERATION_TASKS.clear()
    llm_agent_module._ACTIVE_GENERATION_TASKS.clear()
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.005,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )

    try:
        results = await asyncio.gather(
            *(
                agent._await_generation_operation(
                    stubborn_provider(),
                    controller=llm_agent_module.GenerationBudgetController(
                        agent._resolve_generation_budget_policy()
                    ),
                    streaming=False,
                )
                for _ in range(20)
            ),
            return_exceptions=True,
        )

        assert all(isinstance(result, Exception) for result in results)
        assert len(llm_agent_module._ACTIVE_GENERATION_TASKS) <= 3
        assert len(llm_agent_module._DETACHED_GENERATION_TASKS) <= 3
        assert len(provider_tasks) <= 3
    finally:
        release.set()
        if provider_tasks:
            await asyncio.wait_for(asyncio.gather(*provider_tasks), timeout=1)
        await asyncio.sleep(0)
        llm_agent_module._DETACHED_GENERATION_TASKS.clear()
        llm_agent_module._ACTIVE_GENERATION_TASKS.clear()


@pytest.mark.asyncio
async def test_ordinary_runtime_does_not_cap_healthy_generation_concurrency(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("AWORLD_MAX_PENDING_GENERATION_TASKS", raising=False)
    llm_agent_module._DETACHED_GENERATION_TASKS.clear()
    llm_agent_module._ACTIVE_GENERATION_TASKS.clear()
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

    results = await asyncio.gather(
        *(
            agent._await_generation_operation(
                asyncio.sleep(0.01, result=index),
                controller=llm_agent_module.GenerationBudgetController(
                    agent._resolve_generation_budget_policy()
                ),
                streaming=False,
            )
            for index in range(16)
        )
    )

    assert results == list(range(16))


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


@pytest.mark.asyncio
async def test_truncated_action_repair_is_typed_as_exhausted(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = 0

    async def fake_stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            while True:
                await asyncio.sleep(0.002)
                yield ModelResponse(
                    id="primary", model="fake-model", content="planning "
                )
        yield ModelResponse(id="repair", model="fake-model", content="still planning")
        yield ModelResponse(
            id="repair", model="fake-model", finish_reason="length"
        )

    monkeypatch.setattr(llm_agent_module, "acall_llm_model_stream", fake_stream)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.1,
            active_tool_free_timeout_seconds=0.01,
            action_repair_timeout_seconds=0.1,
            action_repair_max_output_tokens=64,
        )
    )
    message = _message("truncated-repair")

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "create the artifact"}],
            message=message,
            stream=True,
        )

    assert raised.value.reason is GenerationStopReason.ACTION_REPAIR_EXHAUSTED
    assert raised.value.partial_response.finish_reason == "length"
    assert calls == 2
    events = message.context.context_info["generation_budget_events"]
    assert events[-1]["reason"] == GenerationStopReason.ACTION_REPAIR_EXHAUSTED.value


def test_context_overflow_preserves_latest_answer_without_cancellation() -> None:
    response = Agent._context_overflow_response(
        [
            {"role": "user", "content": "work"},
            {"role": "assistant", "content": "partial useful answer"},
            {"role": "tool", "content": "large output"},
        ]
    )

    assert response.content == "partial useful answer"
    assert response.message["aworld_incomplete_reason"] == "context_window_exceeded"
    assert response.message["aworld_recoverable"] is False
