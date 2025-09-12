# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.agents.llm_agent import Agent
from aworld.config import RunConfig, EngineName
from aworld.core.task import Task
from aworld.runner import Runners
from examples.tutorials.run import question


def build_agent():
    return Agent(
        name="Research Agent",
        system_prompt="You specialize at researching.",
    )


if __name__ == "__main__":
    # running on ray is for more tasks `Runners.sync_run_task(task=[task1, task2, ...])
    # need install Ray first, pip install ray

    tasks = [Task(input=question, agent=build_agent(), id="abcd"),
             Task(input=question, agent=build_agent(), id="efgh"),
             Task(input=question, agent=build_agent(), id="ijkl")]
    res = Runners.sync_run_task(task=tasks,
                                run_conf=RunConfig(engine_name=EngineName.RAY, worker_num=len(tasks)))
    # Hello world!
    [print(k, ": ", v.answer) for k, v in res.items()]
