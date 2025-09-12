# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.config import RunConfig, EngineName
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from examples.multi_agents.collaborative.simple_demo.run import agent, agent1

if __name__ == "__main__":
    res = Runners.sync_run(input="use tool agent say", swarm=Swarm(agent, register_agents=[agent1]),
                           run_conf=RunConfig(engine_name=EngineName.RAY))
    # hello world
    print(res.answer)