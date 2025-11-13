import asyncio
from aworld.experimental.a2a.client_proxy import A2AClientProxy
from aworld.experimental.a2a.config import ClientConfig
from aworld.core.task import Task


from aworld.logs.util import logger


async def run():
    client = A2AClientProxy(agent_card="http://localhost:7500", config=ClientConfig(streaming=False))
    resp = await client.get_or_init_agent_card()
    logger.info(f"get_agent_card resp: {resp}")

    resp = await client.send_task(
        task=Task(
            id="test_task",
            input="What is the mcp.",
            session_id="test_session",
        )
    )
    logger.info(f"send_task resp: {resp}")


async def run_stream():
    client = A2AClientProxy(agent_card="http://localhost:7500", config=ClientConfig(streaming=True))
    resp = await client.get_or_init_agent_card()
    logger.info(f"get_agent_card resp: {resp}")
    async for event in client.send_task_stream(
        task=Task(
            id="test_task",
            input="Search baidu, and then answer the question: What is the mcp.",
            session_id="test_session",
        )
    ):
        logger.info(f"send_task event: {event}")

# if __name__ == "__main__":
#     # asyncio.run(run())
#     asyncio.run(run_stream())
