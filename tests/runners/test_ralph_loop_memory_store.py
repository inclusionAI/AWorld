from pathlib import Path

import pytest

from aworld.runners.ralph.memory import LoopMemoryStore
from aworld.runners.ralph.state import LoopContext, LoopState
from aworld.runners.ralph.types import CompletionCriteria


@pytest.mark.asyncio
async def test_loop_memory_store_round_trips_iteration_summary(tmp_path):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    store = LoopMemoryStore(context)

    await store.write_iteration_summary("task-1", 1, "summary text")

    assert await store.read_iteration_summary("task-1", 1) == "summary text"


@pytest.mark.asyncio
async def test_loop_memory_store_uses_file_storage_for_answer_payloads(tmp_path):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    store = LoopMemoryStore(context)

    await store.write_answer("task-1", 2, "answer text")

    assert await store.read_answer("task-1", 2) == "answer text"
    assert Path(tmp_path, "task", "answer", "task-1_2").exists()


def test_loop_context_enables_filesystem_and_terminal_sandbox_tools(tmp_path):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )

    assert set(context.sand_box.mcp_config.get("mcpServers", {})) >= {"filesystem", "terminal"}
