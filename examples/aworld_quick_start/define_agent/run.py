# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import os
# use custom log path
os.environ['AWORLD_LOG_PATH'] = '/tmp/aworld_logs'

from aworld.agents.llm_agent import Agent
from aworld.runner import Runners
from examples.aworld_quick_start.common import agent_config

async def main():
    # Define an agent
    demo = Agent(
        conf=agent_config,
        name="demo_agent",
        system_prompt="You are a help assistant.",
        # desc="description for agent as tool"
        # agent_prompt="for fine-tune",
        # agent_id="for special fix agent id",
    )

    # Run the agent
    res = await Runners.run(input="who are you?", agent=demo)
    print(res.answer)


if __name__ == '__main__':
    asyncio.run(main())
