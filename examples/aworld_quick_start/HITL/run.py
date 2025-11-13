# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from aworld.tools.human.human import HUMAN
from examples.aworld_quick_start.common import agent_config

if __name__ == '__main__':
    # human in the loop
    agent = Agent(
        conf=agent_config,
        name='human_test',
        system_prompt="You are a helpful assistant.",
        tool_names=[HUMAN]
    )

    swarm = Swarm(agent, max_steps=1)
    result = Runners.sync_run(
        input="use human tool to ask a question, e.g. what is the weather in beijing?" \
              "please use HUMAN tool only once",
        swarm=swarm
    )
    print(result)
