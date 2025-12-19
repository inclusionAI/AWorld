# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm, GraphBuildType, TeamSwarm
from aworld.core.task import Task
from aworld.runner import Runners
from examples.aworld_quick_start.common import agent_config, search, summary
from examples.aworld_quick_start.handoff.prompt import system_prompt


async def main():
    plan = Agent(
        conf=agent_config,
        name="example_plan_agent",
        system_prompt=system_prompt,
    )
    goal = """I need a 7-day Beijing itinerary, departing from Hangzhou, We want to see beautiful botanical garden and experience traditional Beijing culture. Please provide a detailed itinerary and create a summary.
            you need search and extract different info 1 times, and then summary, complete the task.
            """
    # swarm = Swarm((plan, search), (plan, summary), build_type=GraphBuildType.HANDOFF)
    # or
    swarm = TeamSwarm(plan, search, summary)

    task = Task(swarm=swarm, input=goal, endless_threshold=5)
    resp = await Runners.run_task(task)
    print(resp.get(task.id).answer)


if __name__ == '__main__':
    asyncio.run(main())
