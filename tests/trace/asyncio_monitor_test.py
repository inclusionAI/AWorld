import traceback
import asyncio
import time

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.trace.config import ObservabilityConfig
from aworld.logs.util import logger
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from aworld.runners.state_manager import RuntimeStateManager
import aworld.trace as trace
from aworld.trace.asyncio_monitor.base import AsyncioMonitor

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


async def run_agent():
    asyncio_monitor = AsyncioMonitor(detect_duration_second=1, shot_file_name=False)
    asyncio_monitor.start()

    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="claude-3-7-sonnet-20250219",
        llm_base_url="",
        llm_api_key="",
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


async def run():
    with AsyncioMonitor(detect_duration_second=1, slow_task_ms=500) as monitor:
        try:
            async def short_task():
                await asyncio.sleep(0.1)
                return "Short task completed"

            async def slow_task():
                await asyncio.sleep(8)
                return "Slow task completed"

            async def waiting_task(wait_event):
                print("Waiting task started")
                await wait_event.wait()
                print("Waiting task resumed")
                return "Waiting task completed"

            async def concurrent_task(task_id):
                print("Concurrent task 3 is sleeping for 3 seconds")
                time.sleep(3)
                return f"Concurrent task {task_id} completed"

            wait_event = asyncio.Event()

            tasks = []
            short_future = asyncio.create_task(short_task())
            tasks.append(short_future)
            slow_future = asyncio.create_task(slow_task())
            tasks.append(slow_future)
            waiting_future = asyncio.create_task(waiting_task(wait_event))
            tasks.append(waiting_future)
            for i in range(1, 6):
                concurrent_future = asyncio.create_task(concurrent_task(i))
                tasks.append(concurrent_future)

            await asyncio.sleep(1.2)
            wait_event.set()

            await asyncio.gather(*tasks)
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(traceback.format_exc())


# if __name__ == "__main__":
#     asyncio.run(run())
