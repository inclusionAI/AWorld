from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.core.task import Task
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


def test_loop_context_direct_construction_preserves_completion_criteria(tmp_path):
    criteria = CompletionCriteria(max_iterations=7)

    context = LoopContext(
        completion_criteria=criteria,
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )

    assert context.completion_criteria is criteria
    assert context.completion_criteria.max_iterations == 7


@pytest.mark.asyncio
async def test_loop_context_add_file_reports_failed_answer_persistence(tmp_path, monkeypatch):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )

    async def fail_write_answer(task_id, iteration, content):
        return {"success": False, "error": "write failed"}

    monkeypatch.setattr(context.memory, "write_answer", fail_write_answer)

    success, _, _ = await context.add_file(filename="task-1_2", content="answer text")

    assert success is False


@pytest.mark.asyncio
async def test_read_to_task_context_reads_answer_via_memory_store_without_direct_path_check(tmp_path, monkeypatch):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )

    async def fake_read_answer(task_id, iteration):
        return "persisted answer"

    async def fake_read_feedback(task_id, iteration):
        return "try again"

    def fail_answer_path(task_id, iteration):
        raise AssertionError("read_to_task_context should not inspect answer_path directly")

    monkeypatch.setattr(context.memory, "read_answer", fake_read_answer)
    monkeypatch.setattr(context.memory, "read_reflection_feedback", fake_read_feedback)
    monkeypatch.setattr(context.memory, "answer_path", fail_answer_path)

    task = Task(input="original task")
    task.id = "task-1"

    result = await context.read_to_task_context(task=task, iter_num=2, reuse_context=True)

    assert result is context
    assert "persisted answer" in task.input
    assert "try again" in task.input


@pytest.mark.asyncio
async def test_write_to_loop_context_preserves_feedback_artifact_metadata(tmp_path):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )

    task_context = SimpleNamespace(get_task=lambda: SimpleNamespace(id="task-1"))

    await context.write_to_loop_context(
        content="reflection text",
        task_context=task_context,
        iter_num=2,
        content_type="reflect",
    )

    artifact = context.workspace.get_artifact_data(context.memory.reflection_feedback_artifact_id("task-1", 2))

    assert artifact is not None
    assert artifact["content"] == "reflection text"
    assert artifact["metadata"]["context_type"] == "reflect"
    assert artifact["metadata"]["task_id"] == "task-1"
    assert artifact["metadata"]["iteration"] == 2
