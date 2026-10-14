# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Replay cleanup must keep tool calls that are still waiting for a result.

Issue #761: ``async_messages_transform`` reclaimed the trailing tool-call
messages whenever the incoming observation was not flagged as a tool result,
dropping the call the observation is paired with and surfacing later as
``tool_calls mismatch!``.
"""

import pytest

from aworld.agents.llm_agent import LLMAgent
from aworld.config import AgentConfig, AgentMemoryConfig
from aworld.core.common import Observation
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.task import Task
from aworld.memory.models import MemoryAIMessage, MessageMetadata
from aworld.models.model_response import ToolCall


class _RecordingMemory:
    """Minimal memory double that records every reclaimed item id."""

    def __init__(self) -> None:
        self.items = []
        self.deleted = []
        # The replay path reads pending messages through ``memory_store``.
        self.memory_store = self

    async def add(self, item, agent_memory_config=None):
        self.items.append((item, agent_memory_config))

    def delete(self, item_id):
        self.deleted.append(item_id)
        self.items = [(item, cfg) for item, cfg in self.items if item.id != item_id]

    def get_all(self, filters=None):
        return [item for item, _ in self.items]

    def get_last_n(self, *args, **kwargs):
        return [item for item, _ in self.items]


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


def _build_agent() -> LLMAgent:
    return LLMAgent(
        name="Aworld",
        agent_id="agent-1",
        conf=AgentConfig(
            llm_model_name="test-model",
            llm_api_key="test-key",
            memory_config=AgentMemoryConfig(history_rounds=10),
        ),
    )


def _meta() -> MessageMetadata:
    return MessageMetadata(
        session_id="session-1",
        user_id="user-1",
        task_id="task-1",
        agent_id="agent-1",
        agent_name="Aworld",
    )


def _ai_message_with_call(meta: MessageMetadata) -> MemoryAIMessage:
    return MemoryAIMessage(
        content="",
        tool_calls=[
            ToolCall.from_dict(
                {
                    "id": "Researcher:0",
                    "function": {"name": "Researcher", "arguments": "{}"},
                }
            )
        ],
        metadata=meta,
    )


def _use_memory(monkeypatch, memory: _RecordingMemory) -> None:
    monkeypatch.setattr(
        "aworld.agents.llm_agent.MemoryFactory",
        type("MemoryFactory", (), {"instance": staticmethod(lambda: memory)}),
    )


@pytest.mark.asyncio
async def test_transform_keeps_pending_tool_call_for_branded_observation(monkeypatch):
    """The observation names the producer of the trailing call: it is pending."""
    ai_message = _ai_message_with_call(_meta())
    memory = _RecordingMemory()
    memory.items = [(ai_message, None)]
    _use_memory(monkeypatch, memory)

    agent = _build_agent()
    message = Message(headers={"context": _build_context()})

    await agent.async_messages_transform(
        image_urls=[],
        observation=Observation(
            observer="Researcher",
            ability="policy",
            content="research finished",
        ),
        message=message,
    )

    assert memory.deleted == []
    assert ai_message in [item for item, _ in memory.items]


@pytest.mark.asyncio
async def test_transform_still_reclaims_leaked_tool_call_for_unbranded_observation(
    monkeypatch,
):
    """An observation without a producer identity cannot answer the trailing call."""
    ai_message = _ai_message_with_call(_meta())
    memory = _RecordingMemory()
    memory.items = [(ai_message, None)]
    _use_memory(monkeypatch, memory)

    agent = _build_agent()
    message = Message(headers={"context": _build_context()})

    messages = await agent.async_messages_transform(
        image_urls=[],
        observation=Observation(content="continue current turn"),
        message=message,
    )

    assert memory.deleted == [ai_message.id]
    assert not any(item.get("tool_calls") for item in messages)
