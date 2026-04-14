# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from unittest.mock import MagicMock, patch

import pytest

from aworld.config import ConfigDict
from aworld.core.common import ActionModel, Observation
from aworld.core.context.base import Context
from aworld.core.event.base import Constants, Message, TopicType
from aworld.core.task import Task, TaskResponse
from aworld.core.tool.base import AsyncTool
from aworld.runners.handler.tool import DefaultToolHandler
from aworld.runners.task_runner import TaskRunner


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
