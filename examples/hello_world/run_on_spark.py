# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.config import RunConfig, EngineName
from aworld.runner import Runners
from examples.hello_world.run import swarm, question

if __name__ == "__main__":
    res = Runners.sync_run(input=question, swarm=swarm,
                           run_conf=RunConfig(engine_name=EngineName.SPARK, in_local=True))
    # Hello world!
    print(res.answer)
