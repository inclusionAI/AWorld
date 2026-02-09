# coding: utf-8
# Copyright (c) inclusionAI.
from aworld.core.context.amni import AmniConfigFactory, ApplicationContext, TaskInput
from aworld.core.context.amni.config import AmniConfigLevel, BaseConfig
from aworld.core.context.base import Context
from aworld.core.task import Task


async def create_context(task: Task, context_config: BaseConfig = None) -> Context:
    if not context_config:
        context_config = AmniConfigFactory.create(AmniConfigLevel.NAVIGATOR)
    task_input = TaskInput(
        user_id=task.user_id,
        session_id=task.session_id,
        task_id=task.id,
        task_content=task.input,
        origin_user_input=task.input
    )
    context = await ApplicationContext.from_input(task_input, context_config=context_config)

    if task.agent:
        await context.build_agents_state([task.agent])
    if task.swarm:
        await context.build_agents_state(task.swarm.topology)

    return context
