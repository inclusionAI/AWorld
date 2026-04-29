from pathlib import Path

import pytest

from aworld.core.context import ApplicationContext
from aworld.core.context.amni.prompt.neurons import aworld_file_neuron as aworld_file_neuron_module
from aworld.core.context.amni.prompt.neurons import relevant_memory_neuron as relevant_memory_neuron_module
from aworld.core.context.amni.prompt.neurons.aworld_file_neuron import AWORLDFileNeuron
from aworld.core.context.amni.prompt.neurons.relevant_memory_neuron import RelevantMemoryNeuron
from aworld.core.context.amni.state import (
    ApplicationTaskContextState,
    TaskInput,
    TaskOutput,
    TaskWorkingState,
)
from aworld.core.memory import MemoryConfig
from aworld.memory.db.filesystem import FileSystemMemoryStore
from aworld.memory.main import MemoryFactory
from aworld.plugins.discovery import discover_plugins
from aworld_cli.core.command_system import CommandContext, CommandRegistry
from aworld_cli.memory.bootstrap import register_cli_memory_provider
from aworld_cli.plugin_capabilities.commands import register_plugin_commands
from aworld_cli.plugin_capabilities.hooks import load_plugin_hooks


def _get_builtin_memory_plugin_root() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "aworld-cli"
        / "src"
        / "aworld_cli"
        / "builtin_plugins"
        / "memory_cli"
    )


def _create_test_context(working_dir=None, task_content: str = "Need pnpm guidance"):
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


def _build_hybrid_memory(tmp_path) -> object:
    register_cli_memory_provider()
    return MemoryFactory.from_config(
        config=MemoryConfig(provider="hybrid"),
        memory_store=FileSystemMemoryStore(memory_root=str(tmp_path / "runtime-memory")),
    )


def _patch_memory_factory(monkeypatch: pytest.MonkeyPatch, module, memory: object) -> None:
    memory_factory = type("MemoryFactory", (), {"instance": staticmethod(lambda: memory)})
    monkeypatch.setattr(module, "MemoryFactory", memory_factory, raising=False)


@pytest.mark.asyncio
async def test_remembered_workspace_guidance_is_visible_to_later_prompt_augmentation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        remember = CommandRegistry.get("remember")
        assert remember is not None
        await remember.execute(
            CommandContext(
                cwd=str(workspace),
                user_args="Use pnpm for workspace package management",
            )
        )
    finally:
        CommandRegistry.restore(snapshot)

    memory = _build_hybrid_memory(tmp_path)
    _patch_memory_factory(monkeypatch, aworld_file_neuron_module, memory)

    neuron = AWORLDFileNeuron()
    formatted = await neuron.format(_create_test_context(working_dir=str(workspace)))

    assert "Use pnpm for workspace package management" in formatted
    assert (workspace / ".aworld" / "AWORLD.md").exists()


@pytest.mark.asyncio
async def test_workspace_memory_overrides_global_guidance_by_later_prompt_order(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(Path, "home", lambda: home)

    (home / ".aworld").mkdir(parents=True)
    (home / ".aworld" / "AWORLD.md").write_text("Use npm for package management.", encoding="utf-8")
    (workspace / ".aworld").mkdir(parents=True)
    (workspace / ".aworld" / "AWORLD.md").write_text(
        "Use pnpm for package management in this workspace.",
        encoding="utf-8",
    )

    memory = _build_hybrid_memory(tmp_path)
    _patch_memory_factory(monkeypatch, aworld_file_neuron_module, memory)

    neuron = AWORLDFileNeuron()
    formatted = await neuron.format(_create_test_context(working_dir=str(workspace)))

    assert "Use npm for package management." in formatted
    assert "Use pnpm for package management in this workspace." in formatted
    assert formatted.index("Use npm for package management.") < formatted.index(
        "Use pnpm for package management in this workspace."
    )


@pytest.mark.asyncio
async def test_session_log_only_memory_does_not_mutate_primary_instruction_file(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-1",
            "task_status": "idle",
            "workspace_path": str(workspace),
            "final_answer": "Temporary debug note for the current task only.",
        },
        state={"workspace_path": str(workspace)},
    )

    assert not (workspace / ".aworld" / "AWORLD.md").exists()
    assert (workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl").exists()


@pytest.mark.asyncio
async def test_relevant_recall_injects_only_matching_session_log_memories(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-1",
            "task_status": "idle",
            "workspace_path": str(workspace),
            "final_answer": "Use pnpm and keep tests fast in this workspace.",
        },
        state={"workspace_path": str(workspace)},
    )
    await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-2",
            "task_status": "idle",
            "workspace_path": str(workspace),
            "final_answer": "Coordinate launch notes with the marketing team.",
        },
        state={"workspace_path": str(workspace)},
    )

    memory = _build_hybrid_memory(tmp_path)
    _patch_memory_factory(monkeypatch, relevant_memory_neuron_module, memory)

    neuron = RelevantMemoryNeuron()
    formatted = await neuron.format(
        _create_test_context(
            working_dir=str(workspace),
            task_content="Need pnpm and tests guidance for this workspace",
        )
    )

    assert "Relevant Memory Recall" in formatted
    assert "Use pnpm and keep tests fast in this workspace." in formatted
    assert "Coordinate launch notes with the marketing team." not in formatted
