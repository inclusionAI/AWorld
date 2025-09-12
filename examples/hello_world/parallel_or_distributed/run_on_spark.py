# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.agents.llm_agent import Agent
from aworld.config import RunConfig, EngineName
from aworld.core.task import Task
from aworld.runner import Runners
from examples.hello_world.run import question


def build_agent():
    return Agent(
        name="Research Agent",
        system_prompt="You specialize at researching.",
    )


if __name__ == "__main__":
    # running on spark is for more tasks `Runners.sync_run_task(task=[task1, task2, ...])
    # need install Pyspark first, such as pip install pyspark==3.5.0 in JDK 1.8.0_441

    tasks = [Task(input=question, agent=build_agent(), id="abcd"),
             Task(input=question, agent=build_agent(), id="efgh"),
             Task(input=question, agent=build_agent(), id="ijkl")]
    res = Runners.sync_run_task(task=tasks,
                                run_conf=RunConfig(engine_name=EngineName.SPARK, in_local=True))
    # Hello world!
    [print(k, ": ", v.answer) for k, v in res.items()]
