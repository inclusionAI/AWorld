# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aworld.config import ConfigDict
from aworld.core.common import ActionModel, Observation, TaskItem
from aworld.core.context.base import Context
from aworld.core.event.base import Constants, Message, TopicType
from aworld.core.task import Task, TaskResponse
from aworld.core.tool.base import AsyncTool
from aworld.runners.handler.tool import DefaultToolHandler
from aworld.runners.handler.task import DefaultTaskHandler
from aworld.runners.task_runner import TaskRunner
from aworld.runners.event_runner import TaskEventRunner


class SyncResetAsyncTool(AsyncTool):
    """AsyncTool variant with a synchronous reset to cover mixed implementations."""

    def __init__(self, conf=None, **kwargs):
        self.reset_calls = 0
        super().__init__(conf=conf or ConfigDict({}), **kwargs)

    def reset(self, *, seed: int | None = None, options=None):
        self.reset_calls += 1
        return Observation(content="tool-ready"), {}

    async def do_step(self, action, **kwargs):
        return Observation(content="done"), 1.0, False, False, {}

    async def close(self):
        return None


class DummyTaskRunner(TaskRunner):
    async def do_run(self, context=None) -> TaskResponse:
        return TaskResponse(success=True, answer="ok")

    async def streaming(self):
        if False:
            yield None


@pytest.mark.asyncio
async def test_default_tool_handler_accepts_sync_reset_for_async_tool():
    runner = MagicMock()
    runner.tools = {}
    runner.tools_conf = {}
    runner.event_mng.get_handlers.return_value = {}

    handler = DefaultToolHandler(runner)
    tool = SyncResetAsyncTool(name="async_broken_tool")

    message = Message(
        category=Constants.TOOL,
        payload=[
            ActionModel(
                tool_name="async_broken_tool",
                tool_call_id="call-1",
                agent_name="root-agent",
            )
        ],
        session_id="session-1",
        headers={"context": Context()},
    )

    with patch("aworld.runners.handler.tool.ToolFactory", return_value=tool):
        outputs = [msg async for msg in handler._do_handle(message)]

    assert tool.reset_calls == 1
    assert runner.tools["async_broken_tool"] is tool
    assert tool.context is message.context
    assert any(msg.topic == TopicType.SUBSCRIBE_TOOL for msg in outputs)
    assert any(msg.category == Constants.TOOL and msg.receiver == "async_broken_tool" for msg in outputs)


@pytest.mark.asyncio
async def test_default_tool_handler_prioritizes_dynamic_subscription_before_tool_execution():
    runner = MagicMock()
    runner.tools = {}
    runner.tools_conf = {}
    runner.event_mng.get_handlers.return_value = {}

    handler = DefaultToolHandler(runner)
    tool = SyncResetAsyncTool(name="async_broken_tool")

    message = Message(
        category=Constants.TOOL,
        payload=[
            ActionModel(
                tool_name="async_broken_tool",
                tool_call_id="call-1",
                agent_name="root-agent",
            )
        ],
        session_id="session-1",
        headers={"context": Context()},
    )

    with patch("aworld.runners.handler.tool.ToolFactory", return_value=tool):
        outputs = [msg async for msg in handler._do_handle(message)]

    subscribe_message = next(msg for msg in outputs if msg.topic == TopicType.SUBSCRIBE_TOOL)
    tool_message = next(msg for msg in outputs if msg.category == Constants.TOOL and msg.receiver == "async_broken_tool")

    assert subscribe_message.priority < tool_message.priority


@pytest.mark.asyncio
async def test_task_event_runner_wires_tool_callback_handler():
    task = Task(
        id="task-tool-callback",
        name="task-tool-callback",
        input="hello",
        observation=Observation(content=[]),
        context=Context(),
        conf=ConfigDict(),
    )

    runner = TaskEventRunner(task, agent_oriented=False)
    await runner.pre_run()

    assert any(handler.__class__.__name__ == "ToolCallbackHandler" for handler in runner.handlers)


@pytest.mark.asyncio
async def test_default_task_handler_sanitizes_internal_tool_mismatch_errors():
    runner = MagicMock()
    runner.task = SimpleNamespace(max_retry_count=0, hooks=None, is_sub_task=False, id="task-1")
    runner.context = Context()
    runner.start_time = 0.0
    runner.stop = AsyncMock()
    runner.should_stop_task = AsyncMock(return_value=False)

    handler = DefaultTaskHandler(runner)
    context = Context()
    context.set_task(SimpleNamespace(timeout=0))
    message = Message(
        category=Constants.TASK,
        payload=TaskItem(
            msg="AWorldRuntimeException: tool_calls mismatch! CONTEXT_TOOL__list_sessions:0 not found in [], messages: [{'role': 'system', 'content': 'secret'}]",
            data=None,
            stop=True,
        ),
        session_id="session-1",
        topic=TopicType.ERROR,
        headers={"context": context},
    )

    outputs = [msg async for msg in handler.handle(message)]

    response = outputs[-1].payload
    assert "tool_calls mismatch" not in response.answer
    assert "messages:" not in response.answer
    assert "internal" in response.answer.lower()


@pytest.mark.asyncio
async def test_task_runner_accepts_sync_reset_for_async_tool():
    tool = SyncResetAsyncTool(name="async_broken_tool")
    swarm = MagicMock()
    swarm.agents = {}
    swarm.reset = MagicMock()

    task = Task(
        input="hello",
        swarm=swarm,
        tools=[tool],
        tool_names=[],
        context=Context(),
        conf=ConfigDict(),
    )

    runner = DummyTaskRunner(task)
    await runner.pre_run()

    assert tool.reset_calls == 1
    assert tool.context is runner.context
    assert runner.observation.content == "tool-ready"
