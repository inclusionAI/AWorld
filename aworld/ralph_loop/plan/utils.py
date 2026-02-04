# coding: utf-8
# Copyright (c) inclusionAI.
from typing import List

from aworld.core.task import Task
from aworld.ralph_loop.plan.types import StrategicPlan
# from aworld.ralph_loop.schedule.types import ScheduledTask


async def parse_plan_to_tasks(plan: StrategicPlan) -> List[Task]:
    steps = plan.steps
    if not steps:
        return []

    # todo
    tasks = []
    for step in steps:
        task = Task(input=step.description)

    return tasks
