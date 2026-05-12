from types import SimpleNamespace

import pytest

from aworld.config import AgentMemoryConfig
from aworld.core.common import ActionResult
from aworld.core.context.base import Context
from aworld.core.task import Task
from aworld.runners.handler.memory import DefaultMemoryHandler


class _FakeMemory:
    def __init__(self) -> None:
        self.items = []

    async def add(self, item, agent_memory_config=None):
        self.items.append((item, agent_memory_config))


class _FakeAgent:
    def __init__(self, memory_config: AgentMemoryConfig | None = None) -> None:
        self.memory_config = memory_config or AgentMemoryConfig()

    def id(self) -> str:
        return "agent-1"

    def name(self) -> str:
        return "Aworld"


def _build_handler() -> DefaultMemoryHandler:
    runner = SimpleNamespace(task=SimpleNamespace(hooks={}))
    return DefaultMemoryHandler(runner)


def _build_context() -> Context:
    context = Context()
    context.set_task(
        Task(
            id="task-1",
            session_id="session-1",
            user_id="user-1",
            input="test",
        )
    )
    return context


@pytest.mark.asyncio
async def test_default_memory_handler_compacts_large_tool_results(monkeypatch):
    fake_memory = _FakeMemory()
    monkeypatch.setattr(
        "aworld.runners.handler.memory.MemoryFactory",
        type("MemoryFactory", (), {"instance": staticmethod(lambda: fake_memory)}),
    )

    handler = _build_handler()
    agent = _FakeAgent(
        AgentMemoryConfig(
            tool_result_offload=True,
            tool_result_length_threshold=20,
            tool_result_preview_chars=80,
        )
    )
    context = _build_context()
    raw_output = "HEADER\n" + ("A" * 120) + "\nFOOTER"

    await handler._do_add_tool_result_to_memory(
        agent,
        "call-1",
        ActionResult(
            content=raw_output,
            tool_call_id="call-1",
            tool_name="terminal",
            action_name="exec",
            success=True,
            metadata={},
        ),
        context,
    )

    stored_item, stored_config = fake_memory.items[0]
    compaction = stored_item.metadata["ext_info"]["tool_result_compaction"]

    assert stored_config is agent.memory_config
    assert stored_item.content != raw_output
    assert "Tool output compacted for context reuse." in stored_item.content
    assert "terminal" in stored_item.content
    assert "exec" in stored_item.content
    assert "HEADER" in stored_item.content
    assert "FOOTER" in stored_item.content
    assert compaction["applied"] is True
    assert compaction["original_content"] == raw_output
    assert compaction["original_token_count"] > 20


@pytest.mark.asyncio
async def test_default_memory_handler_prefers_tool_summary_when_compacting(monkeypatch):
    fake_memory = _FakeMemory()
    monkeypatch.setattr(
        "aworld.runners.handler.memory.MemoryFactory",
        type("MemoryFactory", (), {"instance": staticmethod(lambda: fake_memory)}),
    )

    handler = _build_handler()
    agent = _FakeAgent(
        AgentMemoryConfig(
            tool_result_offload=True,
            tool_result_length_threshold=20,
            tool_result_preview_chars=60,
        )
    )
    context = _build_context()
    summary = "Command completed and wrote the requested file successfully."

    await handler._do_add_tool_result_to_memory(
        agent,
        "call-2",
        ActionResult(
            content="X" * 160,
            tool_call_id="call-2",
            tool_name="terminal",
            action_name="exec",
            success=True,
            metadata={"tool_use_summary": summary, "offload": True},
        ),
        context,
    )

    stored_item, _ = fake_memory.items[0]
    compaction = stored_item.metadata["ext_info"]["tool_result_compaction"]

    assert stored_item.metadata["summary_content"] == summary
    assert f"Summary: {summary}" in stored_item.content
    assert compaction["summary_content"] == summary
    assert compaction["applied"] is True


@pytest.mark.asyncio
async def test_default_memory_handler_keeps_small_tool_results_unchanged(monkeypatch):
    fake_memory = _FakeMemory()
    monkeypatch.setattr(
        "aworld.runners.handler.memory.MemoryFactory",
        type("MemoryFactory", (), {"instance": staticmethod(lambda: fake_memory)}),
    )

    handler = _build_handler()
    agent = _FakeAgent(
        AgentMemoryConfig(
            tool_result_offload=True,
            tool_result_length_threshold=200,
        )
    )
    context = _build_context()

    await handler._do_add_tool_result_to_memory(
        agent,
        "call-3",
        ActionResult(
            content="short output",
            tool_call_id="call-3",
            tool_name="terminal",
            action_name="exec",
            success=True,
            metadata={},
        ),
        context,
    )

    stored_item, _ = fake_memory.items[0]

    assert stored_item.content == "short output"
    assert "tool_result_compaction" not in stored_item.metadata["ext_info"]
