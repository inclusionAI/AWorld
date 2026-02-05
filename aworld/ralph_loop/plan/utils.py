# coding: utf-8
# Copyright (c) inclusionAI.
from typing import List

from aworld.ralph_loop.plan.types import StrategicPlan
from aworld.ralph_loop.schedule.types import ScheduledTask


async def parse_plan_to_tasks(plan: StrategicPlan) -> List[ScheduledTask]:
    """Transform the `StrategicPlan` structure to a schedule task list.

    Args:
        plan: StrategicPlan instance.

    Returns:
        ScheduledTask list, containing dependency relationships and metadata.
    """
    steps = plan.steps
    if not steps:
        return []

    tasks = []
    for step in steps:
        dependencies = plan.dependency_graph.get(step.step_id, [])

        task = ScheduledTask(
            id=step.step_id,
            input=step.description,
            dependencies=dependencies,
            estimated_time=step.estimated_time,
            metadata={
                "title": step.title,
                "level": step.level,
                "complexity": step.complexity,
                "success_criteria": step.success_criteria,
                "resources_needed": step.resources_needed,
                "alternatives": step.alternatives,
                "plan_id": plan.plan_id,
                **step.metadata
            }
        )
        tasks.append(task)

    return tasks
