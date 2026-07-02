from pathlib import Path

import pytest

from aworld.core.context import ApplicationContext
from aworld.core.context.amni.prompt.neurons import relevant_memory_neuron as relevant_memory_neuron_module
from aworld.core.context.amni.prompt.neurons.relevant_memory_neuron import RelevantMemoryNeuron
from aworld.core.context.amni.state import (
    ApplicationTaskContextState,
    TaskInput,
    TaskOutput,
    TaskWorkingState,
)
from aworld_cli.memory.provider import RelevantMemoryContext


def create_test_context(working_dir=None, task_content: str = "Need pnpm guidance"):
    task_input = TaskInput(session_id="test_session", task_id="test_task", task_content=task_content)
    working_state = TaskWorkingState(messages=[], user_profiles=[], kv_store={})
    task_state = ApplicationTaskContextState(
        task_input=task_input,
        working_state=working_state,
        task_output=TaskOutput(),
    )
    context = ApplicationContext(task_state=task_state)
    if working_dir:
        context.working_directory = working_dir
    return context


@pytest.mark.asyncio
async def test_relevant_memory_neuron_formats_recalled_session_logs(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    class ProviderBackedMemory:
        def get_relevant_memory_context(self, workspace_path, query, limit=3):
            assert Path(workspace_path) == workspace
            assert "pnpm" in query
            return RelevantMemoryContext(
                texts=("Use pnpm and keep tests fast.",),
                source_files=(),
            )

    memory_factory = type(
        "MemoryFactory",
        (),
        {"instance": staticmethod(lambda: ProviderBackedMemory())},
    )
    monkeypatch.setattr(
        relevant_memory_neuron_module,
        "MemoryFactory",
        memory_factory,
        raising=False,
    )

    context = create_test_context(working_dir=str(workspace), task_content="Need pnpm guidance")
    neuron = RelevantMemoryNeuron()

    items = await neuron.format_items(context)
    formatted = await neuron.format(context, items=items)

    assert items == ["Use pnpm and keep tests fast."]
    assert "Relevant Memory Recall" in formatted
    assert "Use pnpm and keep tests fast." in formatted


@pytest.mark.asyncio
async def test_relevant_memory_neuron_skips_when_provider_unavailable(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    memory_factory = type(
        "MemoryFactory",
        (),
        {"instance": staticmethod(lambda: object())},
    )
    monkeypatch.setattr(
        relevant_memory_neuron_module,
        "MemoryFactory",
        memory_factory,
        raising=False,
    )

    context = create_test_context(working_dir=str(workspace))
    neuron = RelevantMemoryNeuron()

    assert await neuron.format_items(context) == []

