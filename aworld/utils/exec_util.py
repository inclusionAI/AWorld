# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import uuid
from typing import List

from aworld.agents.llm_agent import Agent
from aworld.config import RunConfig
from aworld.core.context.base import Context
from aworld.core.task import Task
from aworld.runners.utils import choose_runners, execute_runner


async def exec_tasks(question: str, agents: List[Agent], context: Context):
    tasks = []
    if agents:
        for agent in agents:
            task_id = uuid.uuid4().hex
            if context:
                context.task_id = task_id
            tasks.append(Task(id=task_id, input=question, agent=agent, context=context))

    if not tasks:
        raise RuntimeError("no task need to run in parallelizable agent.")

    runners = await choose_runners(tasks)
    res = await execute_runner(runners, RunConfig(reuse_process=False))

    results = []
    for task in tasks:
        results.append(res.get(task.id))
    return results
