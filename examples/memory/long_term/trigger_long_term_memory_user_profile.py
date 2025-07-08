import asyncio
import os

from dotenv import load_dotenv

from aworld.core.memory import LongTermConfig, MemoryConfig
from aworld.memory.main import MemoryFactory
from aworld.memory.models import LongTermMemoryTriggerParams, MessageMetadata
from aworld.memory.db.postgres import PostgresMemoryStore
from examples.memory.short_term.utils import add_mock_messages


async def trigger_long_term_memory_user_profile():
    load_dotenv()
    postgres_memory_store = PostgresMemoryStore(db_url=os.getenv("MEMORY_STORE_POSTGRES_DSN"))
    MemoryFactory.init(custom_memory_store=postgres_memory_store)

    memory = MemoryFactory.instance()
    metadata = MessageMetadata(
        user_id="zues",
        session_id="session#foo",
        task_id="zues:session#foo:task#1",
        agent_id="super_agent",
        agent_name="super_agent"
    )

    await add_mock_messages(memory, metadata)
    memory_config = MemoryConfig(
            enable_long_term=True,
            long_term_config=LongTermConfig.create_simple_config(
                enable_user_profiles=True
            )
        )
    await memory.trigger_short_term_memory_to_long_term(LongTermMemoryTriggerParams(
        agent_id=metadata.agent_id,
        session_id=metadata.session_id,
        task_id=metadata.task_id,
        user_id=metadata.user_id,
        force=True
    ), memory_config)



    """
    [
    {
        "key": "skills.technical",
        "value": {
            "gaming_skills": ["League of Legends"]
        }
    },
    {
        "key": "goals.learning",
        "value": {
            "target": "improve gaming skills in League of Legends"
        }
    }
    ]
    """
    await asyncio.sleep(10)

async def query_user_profile():
    load_dotenv()
    postgres_memory_store = PostgresMemoryStore(db_url=os.getenv("MEMORY_STORE_POSTGRES_DSN"))
    MemoryFactory.init(custom_memory_store=postgres_memory_store)

    memory = MemoryFactory.instance()
    metadata = MessageMetadata(
        user_id="zues",
        session_id="session#foo",
        task_id="zues:session#foo:task#1",
        agent_id="super_agent",
        agent_name="super_agent"
    )
    user_profiles = await memory.retrival_user_profile(
        user_id=metadata.user_id,
        user_input="what is my advantage skills?"
    )
    for user_profile in user_profiles:
        print(user_profile)



if __name__ == '__main__':
    asyncio.run(trigger_long_term_memory_user_profile())
    asyncio.run(query_user_profile())

