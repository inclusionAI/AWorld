from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import LLMAgent
from aworld.config import AgentConfig, AgentMemoryConfig
from aworld.core.common import ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.task import Task
from aworld.memory.models import MemoryAIMessage, MemoryToolMessage, MessageMetadata
from aworld.models.model_response import ToolCall
from aworld.runners.handler.memory import DefaultMemoryHandler


class _FakeMemory:
    def __init__(self) -> None:
        self.items = []

    async def add(self, item, agent_memory_config=None):
        self.items.append((item, agent_memory_config))

    def get_all(self, filters=None):
        if not filters:
            return [item for item, _ in self.items]

        matched = []
        for item, _ in self.items:
            if filters.get("agent_id") is not None and item.metadata.get("agent_id") != filters["agent_id"]:
                continue
            if filters.get("session_id") is not None and item.metadata.get("session_id") != filters["session_id"]:
                continue
            if filters.get("task_id") is not None and item.metadata.get("task_id") != filters["task_id"]:
                continue
            if filters.get("tool_call_id") is not None and item.metadata.get("tool_call_id") != filters["tool_call_id"]:
                continue
            if filters.get("memory_type") is not None and item.memory_type != filters["memory_type"]:
                continue
            matched.append(item)
        return matched

    def get_last_n(self, *args, **kwargs):
        return [item for item, _ in self.items]


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


@pytest.mark.asyncio
async def test_default_memory_handler_skips_duplicate_tool_call_id(monkeypatch):
    fake_memory = _FakeMemory()
    monkeypatch.setattr(
        "aworld.runners.handler.memory.MemoryFactory",
        type("MemoryFactory", (), {"instance": staticmethod(lambda: fake_memory)}),
    )

    handler = _build_handler()
    agent = _FakeAgent()
    context = _build_context()

    await handler._do_add_tool_result_to_memory(
        agent,
        "call-duplicate",
        ActionResult(
            content="first output",
            tool_call_id="call-duplicate",
            tool_name="cron",
            action_name="add",
            success=True,
            metadata={},
        ),
        context,
    )
    await handler._do_add_tool_result_to_memory(
        agent,
        "call-duplicate",
        ActionResult(
            content="second output",
            tool_call_id="call-duplicate",
            tool_name="cron",
            action_name="add",
            success=True,
            metadata={},
        ),
        context,
    )

    assert len(fake_memory.items) == 1
    stored_item, _ = fake_memory.items[0]
    assert stored_item.content == "first output"
    assert stored_item.tool_call_id == "call-duplicate"


@pytest.mark.asyncio
async def test_llm_message_replay_skips_duplicate_tool_result(monkeypatch):
    meta = MessageMetadata(
        session_id="session-1",
        user_id="user-1",
        task_id="task-1",
        agent_id="agent-1",
        agent_name="Aworld",
    )
    ai_message = MemoryAIMessage(
        content="",
        tool_calls=[
            ToolCall.from_dict({
                "id": "cron__cron_tool:2",
                "function": {"name": "cron__cron_tool", "arguments": "{}"},
            })
        ],
        metadata=meta,
    )
    first_tool = MemoryToolMessage(
        content="first cron result",
        tool_call_id="cron__cron_tool:2",
        metadata=meta,
    )
    duplicate_tool = MemoryToolMessage(
        content="duplicate cron result",
        tool_call_id="cron__cron_tool:2",
        metadata=meta,
    )
    fake_memory = _FakeMemory()
    fake_memory.items = [(ai_message, None), (first_tool, None), (duplicate_tool, None)]
    monkeypatch.setattr(
        "aworld.agents.llm_agent.MemoryFactory",
        type("MemoryFactory", (), {"instance": staticmethod(lambda: fake_memory)}),
    )

    context = _build_context()
    agent = LLMAgent(
        name="Aworld",
        agent_id="agent-1",
        conf=AgentConfig(
            llm_model_name="test-model",
            llm_api_key="test-key",
            memory_config=AgentMemoryConfig(history_rounds=10),
        ),
    )
    message = Message(headers={"context": context})

    messages = await agent.async_messages_transform(
        image_urls=[],
        observation=Observation(action_result=[ActionResult(content="tool result already recorded")]),
        message=message,
    )

    tool_messages = [message for message in messages if message.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "cron__cron_tool:2"
    assert tool_messages[0]["content"] == [{"type": "text", "text": "first cron result"}]


@pytest.mark.asyncio
async def test_llm_message_replay_drops_incomplete_tool_call_turn(monkeypatch):
    meta = MessageMetadata(
        session_id="session-1",
        user_id="user-1",
        task_id="task-1",
        agent_id="agent-1",
        agent_name="Aworld",
    )
    ai_message = MemoryAIMessage(
        content="",
        tool_calls=[
            ToolCall.from_dict({
                "id": "cron__cron_tool:missing",
                "function": {"name": "cron__cron_tool", "arguments": "{}"},
            })
        ],
        metadata=meta,
    )
    fake_memory = _FakeMemory()
    fake_memory.items = [(ai_message, None)]
    monkeypatch.setattr(
        "aworld.agents.llm_agent.MemoryFactory",
        type("MemoryFactory", (), {"instance": staticmethod(lambda: fake_memory)}),
    )

    context = _build_context()
    agent = LLMAgent(
        name="Aworld",
        agent_id="agent-1",
        conf=AgentConfig(
            llm_model_name="test-model",
            llm_api_key="test-key",
            memory_config=AgentMemoryConfig(history_rounds=10),
        ),
    )
    message = Message(headers={"context": context})

    messages = await agent.async_messages_transform(
        image_urls=[],
        observation=Observation(action_result=[ActionResult(content="continue current turn")]),
        message=message,
    )

    assert not any(message.get("tool_calls") for message in messages)
    assert not any(message.get("role") == "tool" for message in messages)


@pytest.mark.asyncio
async def test_llm_message_replay_skips_orphan_tool_result(monkeypatch):
    meta = MessageMetadata(
        session_id="session-1",
        user_id="user-1",
        task_id="task-1",
        agent_id="agent-1",
        agent_name="Aworld",
    )
    orphan_tool = MemoryToolMessage(
        content="orphan result",
        tool_call_id="missing-call",
        metadata=meta,
    )
    fake_memory = _FakeMemory()
    fake_memory.items = [(orphan_tool, None)]
    monkeypatch.setattr(
        "aworld.agents.llm_agent.MemoryFactory",
        type("MemoryFactory", (), {"instance": staticmethod(lambda: fake_memory)}),
    )

    context = _build_context()
    agent = LLMAgent(
        name="Aworld",
        agent_id="agent-1",
        conf=AgentConfig(
            llm_model_name="test-model",
            llm_api_key="test-key",
            memory_config=AgentMemoryConfig(history_rounds=10),
        ),
    )
    message = Message(headers={"context": context})

    messages = await agent.async_messages_transform(
        image_urls=[],
        observation=Observation(action_result=[ActionResult(content="continue current turn")]),
        message=message,
    )

    assert not any(message.get("role") == "tool" for message in messages)


@pytest.mark.asyncio
async def test_default_memory_handler_compacts_large_tool_results_by_char_length(monkeypatch):
    fake_memory = _FakeMemory()
    monkeypatch.setattr(
        "aworld.runners.handler.memory.MemoryFactory",
        type("MemoryFactory", (), {"instance": staticmethod(lambda: fake_memory)}),
    )

    handler = _build_handler()
    agent = _FakeAgent(
        AgentMemoryConfig(
            tool_result_offload=True,
            tool_result_length_threshold=100000,
            tool_result_preview_chars=120,
        )
    )
    context = _build_context()
    raw_output = "HEADER\n" + ("0123456789" * 900) + "\nFOOTER"

    await handler._do_add_tool_result_to_memory(
        agent,
        "call-4",
        ActionResult(
            content=raw_output,
            tool_call_id="call-4",
            tool_name="terminal",
            action_name="exec",
            success=True,
            metadata={},
        ),
        context,
    )

    stored_item, _ = fake_memory.items[0]

    assert stored_item.content != raw_output
    assert "Tool output compacted for context reuse." in stored_item.content
    assert stored_item.metadata["ext_info"]["tool_result_compaction"]["trigger"] == "char_threshold"
