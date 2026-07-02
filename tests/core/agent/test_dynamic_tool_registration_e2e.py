# coding: utf-8
"""
End-to-end regression for dynamic tool registration followed by a second LLM turn.

This guards the path that previously failed with:
- first tool message consumed before dynamic subscription completed
- duplicate tool execution / duplicate tool-memory writes
- second LLM round crashing with `tool_calls mismatch`
"""

from unittest.mock import patch

import pytest

from aworld.agents.llm_agent import Agent
from aworld.config import ConfigDict
from aworld.config.conf import AgentConfig
from aworld.core.common import Observation
from aworld.core.tool.base import AsyncTool
import aworld.memory.main as memory_main
from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.runner import Runners


class DynamicEchoTool(AsyncTool):
    def __init__(self, conf=None, **kwargs):
        self.execution_count = 0
        super().__init__(conf=conf or ConfigDict({}), **kwargs)

    async def do_step(self, action, **kwargs):
        self.execution_count += 1
        return Observation(content="dynamic tool executed"), 1.0, False, False, {}

    async def close(self):
        return None


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
async def test_dynamic_tool_registration_survives_second_llm_turn_without_tool_call_mismatch():
    llm_call_count = 0
    dynamic_tool = DynamicEchoTool(name="dynamic_echo_tool")

    original_memory_instance = memory_main.MEMORY_HOLDER.get("instance")
    memory_main.MEMORY_HOLDER.clear()
    memory_main.MemoryFactory.init(custom_memory_store=memory_main.InMemoryMemoryStore())

    async def fake_acall_llm_model(_llm, messages, model, temperature, tools, stream, context, **kwargs):
        nonlocal llm_call_count
        llm_call_count += 1

        if llm_call_count == 1:
            return ModelResponse(
                id="resp-dynamic-1",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="call-dynamic-1",
                        function=Function(
                            name="dynamic_echo_tool__echo",
                            arguments="{}",
                        ),
                    )
                ],
            )

        if llm_call_count == 2:
            return ModelResponse(
                id="resp-dynamic-2",
                model="fake-model",
                content="dynamic tool flow completed",
            )

        raise AssertionError(f"Unexpected extra LLM call: {llm_call_count}")

    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
        tool_names=["dynamic_echo_tool"],
        wait_tool_result=True,
        feedback_tool_result=True,
        llm_max_attempts=1,
        llm_retry_delay=0.01,
    )
    agent._llm = object()

    try:
        with patch("aworld.agents.llm_agent.acall_llm_model", fake_acall_llm_model):
            with patch("aworld.runners.handler.tool.ToolFactory", return_value=dynamic_tool):
                response = await Runners.run(
                    input="run the dynamic echo tool",
                    agent=agent,
                )

        assert response.success is True
        assert response.answer == "dynamic tool flow completed"
        assert llm_call_count == 2
        assert dynamic_tool.execution_count == 1
    finally:
        memory_main.MEMORY_HOLDER.clear()
        if original_memory_instance is not None:
            memory_main.MEMORY_HOLDER["instance"] = original_memory_instance
