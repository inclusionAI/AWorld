import asyncio
from aworld.experimental.a2a.client_proxy import A2AClientProxy
from aworld.experimental.a2a.config import ClientConfig
from aworld.core.task import Task
from aworld.experimental.a2a.remote_agent import A2ARemoteAgent
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
import aworld.trace as trace

trace.configure(trace.ObservabilityConfig(trace_server_enabled=True))

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


async def run_swarm():
    local = Agent(name="local_agent", system_prompt="You are a search assistant. You must search related content obout the question on baidu first, then handoff the question to the summary assistant.")
    remote = A2ARemoteAgent(name="remote_agent", agent_card="http://localhost:7500")

    swarm = Swarm(local, remote)

    task = Task(
        id="test_task",
        input="What is the mcp ?",
        session_id="test_session",
        swarm=swarm,
    )

    resp = await Runners.run_task(task)
    logger.info(f"run_task resp: {resp[task.id]}")

if __name__ == "__main__":
    #     # asyncio.run(run())
    #     asyncio.run(run_stream())
    asyncio.run(run_swarm())
