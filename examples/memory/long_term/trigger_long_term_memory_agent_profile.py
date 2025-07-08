import asyncio

from dotenv import load_dotenv

from aworld.core.memory import LongTermConfig, MemoryConfig
from aworld.memory.main import MemoryFactory
from aworld.memory.models import LongTermMemoryTriggerParams, MessageMetadata, MemorySystemMessage, MemoryHumanMessage, \
    MemoryAIMessage
from examples.memory import prompts
from examples.memory.prompts import AGENT_EXPERIENCE_EXTRACTION_PROMPT


async def trigger_long_term_memory_user_profile():
    load_dotenv()
    MemoryFactory.init()
    memory = MemoryFactory.instance()
    metadata = MessageMetadata(
        user_id="zues",
        session_id="session#foo",
        task_id="zues:session#foo:task#1",
        agent_id="super_agent",
        agent_name="super_agent"
    )
    memory.add(MemorySystemMessage(content="You are a helpful agent", metadata=metadata))
    memory.add(MemoryHumanMessage(content="I like to play lol games, can you tell me some advise to improve my skill?",metadata=metadata))
    memory.add(MemoryAIMessage(content="To improve in League of Legends, focus on core mechanics: last-hit minions (CS) to maximize gold, practice smart positioning to avoid ganks, and learn when to trade damage safely. Track enemies using minimap awareness, ward objectives (dragons, herald), and ping missing foes to communicate danger. Prioritize champion mastery: study combos, builds, and in-game roles (e.g., tank, DPS), and adapt items/runes to counter enemy threats.", metadata=metadata))

    memory_config = MemoryConfig(
            provider="inmemory",
            enable_long_term=True,
            long_term_config=LongTermConfig.create_simple_config(
                enable_user_profiles=False,
                enable_agent_experiences=True,
                agent_experience_extraction_prompt=AGENT_EXPERIENCE_EXTRACTION_PROMPT,
                message_threshold=6
            )
        )
    await memory.trigger_short_term_memory_to_long_term(LongTermMemoryTriggerParams(
        agent_id="super_agent",
        session_id="session#foo",
        task_id="zues:session#foo:task#1",
        user_id="zues",
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


if __name__ == '__main__':
    asyncio.run(trigger_long_term_memory_user_profile())

