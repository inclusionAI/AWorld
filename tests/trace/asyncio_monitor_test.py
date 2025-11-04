import traceback
import asyncio

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from typing import List
from aworld.trace.config import ObservabilityConfig
from aworld.logs.util import logger
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from aworld.trace.server import get_trace_server
from aworld.runners.state_manager import RuntimeStateManager, RunNode
import aworld.trace as trace
from aworld.trace.asyncio_monitor import AsyncioMonitor

trace.configure(ObservabilityConfig(trace_server_enabled=True))

search_sys_prompt = "You are a helpful search agent."
search_prompt = """
    Please act as a search agent, constructing appropriate keywords and searach terms, using search toolkit to collect relevant information, including urls, webpage snapshots, etc.

    Here are the question: {task}

    pleas only use one action complete this task, at least results 6 pages.
    """

summary_sys_prompt = "You are a helpful general summary agent."

summary_prompt = """
Summarize the following text in one clear and concise paragraph, capturing the key ideas without missing critical points. 
Ensure the summary is easy to understand and avoids excessive detail.

Here are the content: 
{task}
"""


async def run():
    asyncio_monitor = AsyncioMonitor(detect_duration_second=1, shot_file_name=False)
    asyncio_monitor.start()

    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="claude-3-7-sonnet-20250219",
        llm_base_url="xxx",
        llm_api_key="xxx",
    )

    search = Agent(
        conf=agent_config,
        name="search_agent",
        system_prompt=search_sys_prompt,
        agent_prompt=search_prompt,
        tool_names=["search_api"]
    )

    summary = Agent(
        conf=agent_config,
        name="summary_agent",
        system_prompt=summary_sys_prompt,
        agent_prompt=summary_prompt
    )

    # default is sequence swarm mode
    swarm = Swarm(search, summary, max_steps=1, event_driven=True)

    prefix = "search baidu:"
    # can special search google, wiki, duck go, or baidu. such as:
    # prefix = "search wiki: "
    try:
        res = await Runners.run(
            input=prefix + """What is an agent.""",
            swarm=swarm,
            session_id="123"
        )
        print(res.answer)
    except Exception as e:
        logger.error(traceback.format_exc())

    state_manager = RuntimeStateManager.instance()
    nodes = state_manager.get_nodes("123")
    logger.info(f"session 123 nodes: {nodes}")

    # get_trace_server().join()

    await asyncio.sleep(5)

    asyncio_monitor.stop()


# if __name__ == "__main__":
#     asyncio.run(run())
