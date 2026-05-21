from types import SimpleNamespace

import pytest

from aworld.core.context.base import Context
from aworld.core.event.base import Constants, Message, TopicType
from aworld.core.task import Task
from aworld.runners.handler.agent import DefaultAgentHandler
from aworld.runners.handler.tool import DefaultToolHandler


def _build_context(task_id: str = "task-1") -> Context:
    context = Context(task_id=task_id)
    context.set_task(Task(id=task_id, name=task_id))
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_cls", "runner"),
    [
        (
            DefaultAgentHandler,
            SimpleNamespace(
                swarm=None,
                endless_threshold=3,
                task=Task(id="task-1", name="task-1"),
            ),
        ),
        (
            DefaultToolHandler,
            SimpleNamespace(
                tools={},
                tools_conf={},
                task=Task(id="task-1", name="task-1"),
            ),
        ),
    ],
)
async def test_post_handle_keeps_pending_usage_delta_mergeable(handler_cls, runner):
    parent_context = _build_context()
    parent_context.add_token(
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
        }
    )

    child_context = parent_context.deep_copy()
    child_context.add_token(
        {
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
        }
    )

    handler = handler_cls(runner)
    processed = await handler.post_handle(
        Message(category=Constants.AGENT, headers={"context": parent_context}),
        Message(
            category=Constants.TASK,
            topic=TopicType.FINISHED,
            payload="done",
            headers={"context": child_context},
        ),
    )

    parent_context.merge_context(processed.context)

    assert parent_context.token_usage["prompt_tokens"] == 120
    assert parent_context.token_usage["completion_tokens"] == 15
    assert parent_context.token_usage["total_tokens"] == 135
