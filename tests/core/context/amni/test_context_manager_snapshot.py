from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aworld.core.context.amni.contexts import ContextManager
from aworld.core.context.amni.state import TaskInput


def _manager(memory=None) -> ContextManager:
    manager = ContextManager.model_construct(checkpoint_repo=None)
    object.__setattr__(manager, "_memory", memory or SimpleNamespace(add=AsyncMock()))
    return manager


@pytest.mark.asyncio
async def test_memory_persistence_is_skipped_when_task_has_no_agent_id():
    memory = SimpleNamespace(add=AsyncMock())
    manager = _manager(memory)
    context = SimpleNamespace(
        session_id="session",
        task_id="task",
        task_input_object=TaskInput(
            session_id="session",
            task_id="task",
            task_content="do work",
            model=None,
        ),
    )

    await manager._save_conversations_to_memory(context)

    memory.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_context_waits_for_and_returns_checkpoint(monkeypatch):
    checkpoint = SimpleNamespace(id="checkpoint-1")
    calls = []

    async def save_memory(self, context, **kwargs):
        calls.append("memory")

    async def save_checkpoint(self, context, **kwargs):
        calls.append("checkpoint")
        return checkpoint

    async def refresh_workspace(self, context):
        calls.append("workspace")

    monkeypatch.setattr(ContextManager, "_save_conversations_to_memory", save_memory)
    monkeypatch.setattr(
        ContextManager, "_save_context_checkpoint_async", save_checkpoint
    )
    monkeypatch.setattr(ContextManager, "_refresh_workspace", refresh_workspace)

    result = await _manager().save_context(
        SimpleNamespace(session_id="session", task_id="task")
    )

    assert result is checkpoint
    assert set(calls) == {"memory", "checkpoint", "workspace"}


@pytest.mark.asyncio
async def test_checkpoint_wrapper_does_not_detach_persistence(monkeypatch):
    checkpoint = SimpleNamespace(id="checkpoint-1")
    persisted = AsyncMock(return_value=checkpoint)
    monkeypatch.setattr(ContextManager, "save_context_checkpoint", persisted)
    context = SimpleNamespace(session_id="session", task_id="task")

    result = await _manager()._save_context_checkpoint_async(context, reason="adaptive")

    assert result is checkpoint
    persisted.assert_awaited_once_with(context, reason="adaptive")
