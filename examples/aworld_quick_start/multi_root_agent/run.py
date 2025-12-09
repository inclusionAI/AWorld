# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.task import TaskResponse
from aworld.runner import Runners
from examples.aworld_quick_start.common import search, summary


async def main():
    """
    More than one agent use the same input:
    Input ─┬→ agent1 ┐
        │            ├──→ agent3
        └─→ agent2 ──┘
    """
    agent1 = search
    agent2 = Agent.from_dict(await Agent.to_dict(agent1))
    agent3 = summary

    # agent1 and agent2 use the same input
    swarm = Swarm((agent1, agent3), (agent2, agent3), root_agent=[agent1, agent2])
    result: TaskResponse = await Runners.run(input="what is agent", swarm=swarm)
    print(result.answer)


if __name__ == '__main__':
    asyncio.run(main())
