# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from examples.aworld_quick_start.common import search, summary

if __name__ == "__main__":
    swarm = Swarm(search, summary, max_steps=1)
    # swarm = WorkflowSwarm(search, summary, max_steps=1)

    prefix = ""
    res = Runners.sync_run(
        input=prefix + """What is an agent.""",
        swarm=swarm
    )
    print(res.answer)