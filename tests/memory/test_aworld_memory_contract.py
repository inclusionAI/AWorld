from pathlib import Path

import pytest

from aworld.core.memory import MemoryConfig
from aworld.memory.db.filesystem import FileSystemMemoryStore
from aworld.memory.main import AworldMemory, MemoryFactory
from aworld.memory.models import (
    MemoryAIMessage,
    MemoryHumanMessage,
    MemoryToolMessage,
    MessageMetadata,
)
from aworld.models.model_response import Function, ToolCall


def _metadata() -> MessageMetadata:
    return MessageMetadata(
        agent_id="agent-1",
        agent_name="Aworld",
        session_id="session-1",
        task_id="task-1",
        user_id="user-1",
    )


@pytest.mark.asyncio
async def test_get_last_n_keeps_tool_pair_integrity(tmp_path) -> None:
    memory = AworldMemory(
        memory_store=FileSystemMemoryStore(memory_root=str(tmp_path)),
        config=MemoryConfig(provider="aworld"),
    )
    metadata = _metadata()

    await memory.add(MemoryHumanMessage(content="run a tool", metadata=metadata))
    await memory.add(
        MemoryAIMessage(
            content="calling tool",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    function=Function(name="echo", arguments='{"value":"x"}'),
                )
            ],
            metadata=metadata,
        )
    )
    await memory.add(
        MemoryToolMessage(
            tool_call_id="call-1",
            content={"ok": True},
            metadata=metadata,
        )
    )

    items = memory.get_last_n(
        1,
        filters={
            "agent_id": "agent-1",
            "session_id": "session-1",
            "task_id": "task-1",
        },
    )

    assert [item.metadata["role"] for item in items[-2:]] == ["assistant", "tool"]


def test_search_without_vector_store_remains_empty(tmp_path) -> None:
    memory = AworldMemory(
        memory_store=FileSystemMemoryStore(memory_root=str(tmp_path)),
        config=MemoryConfig(provider="aworld"),
    )

    assert memory.search("anything") == []


def test_build_cli_memory_config_returns_real_memory_config_in_hybrid_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.memory.bootstrap import build_cli_memory_config

    monkeypatch.delenv("AWORLD_CLI_MEMORY_MODE", raising=False)

    config = build_cli_memory_config()

    assert isinstance(config, MemoryConfig)
    assert config.provider == "hybrid"
    assert not hasattr(config, "base_config")


def test_memory_factory_can_build_hybrid_provider(tmp_path) -> None:
    from aworld_cli.memory.bootstrap import register_cli_memory_provider

    register_cli_memory_provider()
    store = FileSystemMemoryStore(memory_root=str(tmp_path / "runtime-memory"))

    memory = MemoryFactory.from_config(
        config=MemoryConfig(provider="hybrid"),
        memory_store=store,
    )

    assert memory.__class__.__name__ == "HybridMemoryProvider"
    assert memory.runtime_memory.__class__.__name__ == "AworldMemory"


def test_memory_factory_init_can_build_hybrid_provider(tmp_path) -> None:
    from aworld_cli.memory.bootstrap import register_cli_memory_provider

    register_cli_memory_provider()
    store = FileSystemMemoryStore(memory_root=str(tmp_path / "runtime-memory"))

    import aworld.memory.main as memory_main

    memory_main.MEMORY_HOLDER.clear()
    MemoryFactory.init(
        custom_memory_store=store,
        config=MemoryConfig(provider="hybrid"),
    )

    memory = MemoryFactory.instance()

    assert memory.__class__.__name__ == "HybridMemoryProvider"
    assert memory.runtime_memory.__class__.__name__ == "AworldMemory"
    memory_main.MEMORY_HOLDER.clear()


def test_registering_cli_memory_provider_does_not_change_lazy_default_instance(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.memory.bootstrap import register_cli_memory_provider

    monkeypatch.delenv("AWORLD_CLI_MEMORY_MODE", raising=False)
    monkeypatch.setenv("AWORLD_MEMORY_ROOT", str(tmp_path / "lazy-runtime-memory"))
    import aworld.memory.main as memory_main

    memory_main.MEMORY_HOLDER.clear()
    register_cli_memory_provider()

    memory = MemoryFactory.instance()

    assert memory.__class__.__name__ == "AworldMemory"
    assert memory.config.provider == "aworld"
    memory_main.MEMORY_HOLDER.clear()


@pytest.mark.asyncio
async def test_hybrid_provider_preserves_aworld_runtime_message_flow(
    tmp_path,
) -> None:
    from aworld_cli.memory.bootstrap import register_cli_memory_provider

    register_cli_memory_provider()
    memory = MemoryFactory.from_config(
        config=MemoryConfig(provider="hybrid"),
        memory_store=FileSystemMemoryStore(memory_root=str(tmp_path / "runtime-memory")),
    )
    metadata = _metadata()

    await memory.add(MemoryHumanMessage(content="hybrid hello", metadata=metadata))

    filters = {
        "agent_id": "agent-1",
        "session_id": "session-1",
        "task_id": "task-1",
    }
    items = memory.get_all(filters=filters)

    assert [item.content for item in items] == ["hybrid hello"]
    assert [item.content for item in memory.get_last_n(1, filters=filters)] == [
        "hybrid hello"
    ]
    assert memory.search("anything") == []


def test_hybrid_provider_exposes_instruction_context(tmp_path, monkeypatch) -> None:
    from aworld_cli.memory.bootstrap import register_cli_memory_provider

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(Path, "home", lambda: home)

    (home / ".aworld").mkdir(parents=True)
    (home / ".aworld" / "AWORLD.md").write_text("global rule", encoding="utf-8")
    (workspace / ".aworld").mkdir(parents=True)
    (workspace / ".aworld" / "AWORLD.md").write_text(
        "workspace rule",
        encoding="utf-8",
    )

    register_cli_memory_provider()
    memory = MemoryFactory.from_config(
        config=MemoryConfig(provider="hybrid"),
        memory_store=FileSystemMemoryStore(memory_root=str(tmp_path / "runtime-memory")),
    )

    context = memory.get_instruction_context(str(workspace))

    assert context.source_files == (
        home / ".aworld" / "AWORLD.md",
        workspace / ".aworld" / "AWORLD.md",
    )
    assert context.warning is None
    assert context.texts == ("global rule", "workspace rule")
