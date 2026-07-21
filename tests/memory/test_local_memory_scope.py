from __future__ import annotations

import asyncio

import pytest

import aworld.agents.llm_agent  # noqa: F401 - initialize context/model imports first
from aworld.memory.main import MEMORY_HOLDER, MemoryFactory
from aworld.memory.scope import LocalMemoryScope


def test_memory_factory_preserves_exact_global_instance_without_scope() -> None:
    previous = MEMORY_HOLDER.get("instance")
    global_memory = object()
    MEMORY_HOLDER["instance"] = global_memory
    try:
        assert MemoryFactory.instance() is global_memory
        assert MemoryFactory.instance() is global_memory
    finally:
        if previous is None:
            MEMORY_HOLDER.pop("instance", None)
        else:
            MEMORY_HOLDER["instance"] = previous


@pytest.mark.asyncio
async def test_concurrent_local_memory_scopes_are_task_local() -> None:
    first_memory = object()
    second_memory = object()
    both_entered = asyncio.Event()
    entered = 0

    async def resolve(memory: object) -> tuple[object, object]:
        nonlocal entered
        async with LocalMemoryScope(memory):
            entered += 1
            if entered == 2:
                both_entered.set()
            await both_entered.wait()

            async def child() -> object:
                await asyncio.sleep(0)
                return MemoryFactory.instance()

            return MemoryFactory.instance(), await asyncio.create_task(child())

    first, second = await asyncio.gather(
        resolve(first_memory),
        resolve(second_memory),
    )

    assert first == (first_memory, first_memory)
    assert second == (second_memory, second_memory)


@pytest.mark.asyncio
async def test_nested_local_memory_scope_restores_parent() -> None:
    parent_memory = object()
    child_memory = object()

    async with LocalMemoryScope(parent_memory):
        assert MemoryFactory.instance() is parent_memory
        async with LocalMemoryScope(child_memory):
            assert MemoryFactory.instance() is child_memory
        assert MemoryFactory.instance() is parent_memory
