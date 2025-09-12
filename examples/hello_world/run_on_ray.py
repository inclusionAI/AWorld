# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.config import RunConfig, EngineName
from aworld.runner import Runners
from examples.hello_world.run import swarm, question

if __name__ == "__main__":
    # running on ray is for more tasks `Runners.sync_run_task(task=[task1, task2, ...])
    # need install Ray first, pip install ray
    res = Runners.sync_run(input=question,
                           swarm=swarm,
                           run_conf=RunConfig(engine_name=EngineName.RAY))
    # Hello world!
    print(res.answer)