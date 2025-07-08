# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import List, Any

from aworld.agents.llm_agent import Agent
from aworld.config import RunConfig
from aworld.core.common import ActionModel
from aworld.core.context.base import Context
from aworld.core.exceptions import AworldException
from aworld.core.task import Task
from aworld.runners.utils import choose_runners, execute_runner


async def run_task(question: Any, agents: List[Agent], context: Context):
    """Run agents in sequence use an input."""
    for idx, agent in enumerate(agents):
        task = Task(is_sub_task=True, input=question, agent=agent, context=context)
        runners = await choose_runners([task])
        res = await execute_runner(runners, RunConfig(reuse_process=False))
        if res:
            v = res.get(task.id)
            action = ActionModel(agent_name=agent.id(), policy_info=v.answer)
            question = action.policy_info
        else:
            raise Exception(f"{agent.id()} execute fail.")


async def run_same_tasks(question: str, agents: List[Agent], context: Context):
    """All agents run the same question, and return answers."""
    questions = [question] * len(agents)
    return await run_tasks(questions, agents, context)


async def run_tasks(questions: List[Any], agents: List[Agent], context: Context):
    tasks = []
    if not agents:
        raise AworldException(f"no agents to exec {questions}")
    if len(questions) != len(agents):
        raise Exception(f"{questions} question size unequals agents size {agents}")

    agent_task = {}
    for idx, agent in enumerate(agents):
        task = Task(input=questions[idx], agent=agent, context=context, is_sub_task=True)
        tasks.append(task)
        agent_task[task.id] = agent.id()

    if not tasks:
        raise RuntimeError("no task need to run in parallel.")

    runners = await choose_runners(tasks)
    res = await execute_runner(runners, RunConfig())
    results = []
    for k, v in res.items():
        results.append(ActionModel(agent_name=agent_task[k], policy_info=v.answer))
    return results
