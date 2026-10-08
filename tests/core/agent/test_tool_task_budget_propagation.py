from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.common import ActionModel
from aworld.core.context.base import Context
from aworld.core.event.base import Constants, Message
from aworld.core.task import Task, TaskResponse
from aworld.sandbox.task_budget import snapshot_task_budget


def _agent() -> Agent:
    return Agent(
        name="budget-parent-agent",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )


@pytest.mark.asyncio
async def test_wait_tool_result_path_passes_parent_after_context_deep_copy(monkeypatch):
    parent = Task(timeout=300, completion_reserve_seconds=30)
    context = Context(task_id=parent.id)
    context.set_task(parent)
    agent = _agent()
    captured = {}

    async def fake_exec_tool(**kwargs):
        captured.update(kwargs)
        return TaskResponse(success=True, answer="ok")

    monkeypatch.setattr("aworld.utils.run_util.exec_tool", fake_exec_tool)
    monkeypatch.setattr(agent, "_add_message_to_memory", AsyncMock())
    monkeypatch.setattr(
        agent, "_add_tool_result_token_ids_to_context", AsyncMock()
    )
    message = Message(
        category=Constants.AGENT,
        sender="user",
        headers={"context": context},
    )
    action = ActionModel(
        tool_name="mcp",
        action_name="terminal__run_code",
        params={"code": "true"},
        agent_name=agent.id(),
        tool_call_id="call-1",
    )

    await agent.execution_tools([action], message)

    assert captured["parent_task"] is parent
    assert captured["context"].get_task() is parent


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["tool", "agent"])
async def test_run_util_child_task_keeps_parent_budget(monkeypatch, entrypoint):
    from aworld.utils import run_util

    parent = Task(timeout=300, completion_reserve_seconds=30)
    context = Context(task_id=parent.id)
    context.set_task(parent)
    captured = {}

    async def choose(tasks, **kwargs):
        captured["task"] = tasks[0]
        return [SimpleNamespace()]

    async def execute(_runners, **kwargs):
        child = captured["task"]
        return {child.id: TaskResponse(success=True, answer="ok")}

    monkeypatch.setattr(run_util, "choose_runners", choose)
    monkeypatch.setattr(run_util, "execute_runner", execute)

    if entrypoint == "tool":
        await run_util.exec_tool(
            "mcp",
            "terminal__run_code",
            {"code": "true"},
            "agent",
            context,
            sub_task=True,
        )
    else:
        await run_util.exec_agent(
            "delegate",
            _agent(),
            context,
            sub_task=True,
        )

    child = captured["task"]
    assert child.parent_task is parent
    assert child.deadline_epoch_seconds == parent.deadline_epoch_seconds
    assert abs(child.remaining_seconds() - parent.remaining_seconds()) < 0.01
    assert snapshot_task_budget(child).completion_reserve_seconds == 30


@pytest.mark.asyncio
async def test_event_driven_tool_context_retains_current_task_after_transport_copy():
    from aworld.runners.handler.tool import DefaultToolHandler

    parent = Task(timeout=300, completion_reserve_seconds=30)
    context = Context(task_id=parent.id)
    context.set_task(parent)
    handler = DefaultToolHandler(
        SimpleNamespace(tools={}, tools_conf={}, event_mng=SimpleNamespace())
    )
    output = Message(
        category=Constants.TOOL,
        payload=[],
        headers={"context": context},
    )

    transported = await handler.post_handle(output, output)

    assert transported.context is not context
    assert transported.context.get_task() is parent


def test_framework_budget_uses_completion_reserve_when_task_does_not_override_it():
    task = Task(timeout=300)

    budget = snapshot_task_budget(task)

    assert budget.completion_reserve_seconds == 15
