import asyncio
from types import SimpleNamespace

import pytest

from aworld.core.event.base import Constants, Message
from aworld.core.trajectory_update_registry import (
    TrajectoryRegistryState,
    TrajectoryUpdateOutcome,
)
from aworld.runners.handler.group import DefaultGroupHandler


@pytest.mark.asyncio
async def test_child_trajectory_import_is_independent_of_handler_event_count(monkeypatch):
    imports = []
    finished = []

    class _Registry:
        def state(self, task_id):
            return None

    class _InputContext:
        trajectory_update_registry = _Registry()

        async def add_task_trajectory(self, task_id, trajectory, **kwargs):
            imports.append((task_id, trajectory, kwargs))
            return TrajectoryUpdateOutcome(True, True, persisted=True)

    class _HandlerContext:
        def merge_sub_context(self, context):
            pass

    class _StateManager:
        async def finish_sub_group(self, group_id, node_id, messages):
            finished.append((group_id, node_id, messages))

    async def inner(results, handlers):
        yield Message(category=Constants.AGENT, headers={})
        yield Message(category=Constants.TASK, headers={})

    response = SimpleNamespace(
        id="child-task",
        answer="answer",
        context=object(),
        trajectory=[{"id": "step-1"}],
    )

    async def completed_response():
        await asyncio.sleep(0)
        return response

    handler = DefaultGroupHandler.__new__(DefaultGroupHandler)
    handler.context = _HandlerContext()
    handler.runner = SimpleNamespace(
        state_manager=_StateManager(),
        handlers=[],
        _inner_handler_process=inner,
    )
    handler.swarm = SimpleNamespace(agents={})
    monkeypatch.setattr(handler, "_get_agent_batch_size", lambda agent_id, message: None)

    input_context = _InputContext()
    input_message = SimpleNamespace(context=input_context, session_id="session")
    agent_tasks = {
        "node-1": {
            "func": completed_response(),
            "metadata": {
                "group_id": "group-1",
                "root_agent_id": "agent-1",
                "root_tool_call_id": "call-1",
            },
        }
    }

    await handler.process_agent_task_parallel(agent_tasks, input_message)

    assert imports == [
        (
            "child-task",
            [{"id": "step-1"}],
            {"finalized_import": True},
        )
    ]
    assert len(finished) == 1
    assert len(finished[0][2]) == 2


@pytest.mark.asyncio
async def test_drained_child_trajectory_is_not_reimported():
    class _Registry:
        def state(self, task_id):
            assert task_id == "child-task"
            return TrajectoryRegistryState.DRAINED

    class _Context:
        trajectory_update_registry = _Registry()

        async def add_task_trajectory(self, *args, **kwargs):
            raise AssertionError("drained child must not be imported again")

    handler = DefaultGroupHandler.__new__(DefaultGroupHandler)
    response = SimpleNamespace(id="child-task", trajectory=[])

    await handler._import_finalized_child_trajectory(_Context(), response)
