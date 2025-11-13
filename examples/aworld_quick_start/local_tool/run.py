# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from aworld.agents.llm_agent import Agent
from aworld.runner import Runners
from examples.aworld_quick_start.common import agent_config
# import here for tool and action register
from examples.aworld_quick_start.local_tool.hello_world_tool import *


async def main():
    agent = Agent(name="hello_world_agent",
                  conf=agent_config,
                  system_prompt="""You are a helpful agent, and must use `hello_world` tool as example.""",
                  tool_names=["hello_world"])

    res = await Runners.run(
        input="use tool return hello world", agent=agent
    )
    print(res.answer)


if __name__ == "__main__":
    asyncio.run(main())
