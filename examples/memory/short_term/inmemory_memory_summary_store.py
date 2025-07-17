import asyncio
import logging
from datetime import datetime

from dotenv import load_dotenv

from aworld.core.memory import AgentMemoryConfig
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MessageMetadata
from examples.memory.utils import init_postgres_memory


async def run():
    load_dotenv()
    init_postgres_memory()
    memory = MemoryFactory.instance()
    metadata = MessageMetadata(
        user_id="user_id",
        session_id="session_id20250716150929",
        task_id="task_id20250716150929",
        agent_id="self_evolving_agent---uuidd0c142uuid",
        agent_name="self_evolving_agent"
    )
    # Get and print all messages
    items = memory.get_all(filters={
        "user_id": metadata.user_id,
        "agent_id": metadata.agent_id,
        "session_id": metadata.session_id,
        "task_id": metadata.task_id
    })

    summary_config = AgentMemoryConfig(
        enable_summary=True,
        summary_rounds=5
    )
    new_task_id = f"{metadata.task_id}__copy__{datetime.now().strftime('%Y%m%d%H%M%S')}"
    for item in items:
        logging.info(f"{type(item)}")
        item.set_task_id(new_task_id)
        await memory.add(item, agent_memory_config=summary_config)


if __name__ == '__main__':
    asyncio.run(run())
