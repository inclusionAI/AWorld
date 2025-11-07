# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import os

from aworld.config import AgentMemoryConfig, ModelConfig
from aworld.core.memory import MemoryConfig
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MessageMetadata, MemoryHumanMessage

import examples.aworld_quick_start
from examples.aworld_quick_start.use_memory.util import add_mock_messages


async def main():
    MemoryFactory.init(config=MemoryConfig(provider="aworld", llm_config=ModelConfig(
        llm_provider=os.getenv("LLM_PROVIDER"),
        llm_model_name=os.getenv("LLM_MODEL_NAME"),
        llm_base_url=os.getenv("LLM_BASE_URL"),
        llm_api_key=os.getenv("LLM_API_KEY"),
    )))
    memory = MemoryFactory.instance()
    metadata = MessageMetadata(
        user_id="zues",
        session_id="session#foo",
        task_id="zues:session#foo:task#1",
        agent_id="super_agent",
        agent_name="super_agent"
    )
    await add_mock_messages(memory, metadata)

    summary_config = AgentMemoryConfig(
        enable_summary=True,
        summary_rounds=2,
        summary_model="xxx"
    )
    await add_mock_messages(memory, metadata, memory_config=summary_config)
    await memory.add(MemoryHumanMessage(content="new_message", metadata=metadata))

    retrival_memory = memory.get_last_n(last_rounds=6, filters={
        "user_id": metadata.user_id,
        "agent_id": metadata.agent_id,
        "session_id": metadata.session_id,
        "task_id": metadata.task_id
    })

    print("==================  RETRIVAL  ==================")
    for item in retrival_memory:
        print(f"{item.memory_type}: {item.content}")


if __name__ == '__main__':
    asyncio.run(main())
