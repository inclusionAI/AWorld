# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from aworld.memory.main import MemoryFactory
from aworld.memory.models import MessageMetadata

from examples.aworld_quick_start.use_memory.util import add_mock_messages


async def main():
    MemoryFactory.init()
    memory = MemoryFactory.instance()
    metadata = MessageMetadata(
        user_id="zues",
        session_id="session#foo",
        task_id="zues:session#foo:task#1",
        agent_id="super_agent",
        agent_name="super_agent"
    )

    await add_mock_messages(memory, metadata)

    # Get and print all messages
    items = memory.get_all(filters={
        "user_id": metadata.user_id,
        "agent_id": metadata.agent_id,
        "session_id": metadata.session_id,
        "task_id": metadata.task_id
    })
    print("==================  MESSAGES  ==================")
    for item in items:
        print(f"{type(item)}: {item.content}")


if __name__ == '__main__':
    asyncio.run(main())
