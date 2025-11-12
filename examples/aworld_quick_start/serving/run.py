# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from aworld.agents.llm_agent import Agent

from aworld.experimental.a2a.config import ServingConfig
from aworld.runner import Runners
from examples.aworld_quick_start.common import agent_config, summary_sys_prompt, summary_prompt


async def main():
    agent = Agent(
        conf=agent_config,
        name="x_agent",
        system_prompt=summary_sys_prompt,
        agent_prompt=summary_prompt,
    )
    serving_config = ServingConfig(port=12345, server_app="grpc", streaming=True)

    await Runners.start_agent_server(agent, serving_config)


if __name__ == '__main__':
    asyncio.run(main())
