
import asyncio

from aworld.experimental.a2a.agent_server import AgentServer
from aworld.experimental.a2a.config import ServingConfig
from aworld.agents.llm_agent import Agent
import aworld.trace as trace
from aworld.trace.config import ObservabilityConfig
from examples.common.tools.apis.search_api import SearchTool

trace.configure(ObservabilityConfig(trace_server_enabled=True))


async def run():

    agent = Agent(name="test_agent",
                  system_prompt="You are a helpful assistant, and you have a search_api tool to help you search the internet.", tool_names=["search_api"])
    # agent_server = AgentServer(agent=agent, config=ServingConfig(port=7500))
    agent_server = AgentServer(agent=agent, config=ServingConfig(port=7500, streaming=True))
    await agent_server.start()

# if __name__ == "__main__":
#     asyncio.run(run())
