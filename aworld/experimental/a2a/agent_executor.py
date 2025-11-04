# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Union

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TextPart
from a2a.utils import new_task

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import TaskItem
from aworld.core.context.base import Context
from aworld.core.event.base import CancelMessage
from aworld.core.task import Task
from aworld.events.util import send_message
from aworld.logs.util import logger
from aworld.utils.run_util import exec_tasks


class AworldAgentExecutor(AgentExecutor):
    def __init__(self, agent: Union[Agent, Swarm]):
        self.agent = agent
        self.streaming = False

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if not context.message:
            raise ValueError(f"No message in context {context}")

        query = context.get_user_input()
        task = context.current_task
        if not task:
            task = new_task(context.message)  # type: ignore
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        aworld_task = Task(input=query, agent=self.agent)

        if self.streaming:
            # todo
            pass
        else:
            resp = await exec_tasks([aworld_task])

            logger.info(f"task: {aworld_task.id} execute finished. {resp.get(aworld_task.id)}")

            final_output = resp.get(aworld_task.id).answer

            output_parts = [Part(root=TextPart(text=final_output))]
            await updater.add_artifact(
                parts=output_parts,
                name='aworld_artifact',
            )
            parts = [Part(root=TextPart(text=final_output))]
            msg = updater.new_agent_message(parts=parts)
            await updater.complete(msg)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        aworld_context: Context = self.agent.context.deep_copy()
        aworld_context.set_task(Task(id=context.task_id, session_id=aworld_context.session_id))

        msg = CancelMessage(
            payload=TaskItem(msg="task timeout.", data=context, stop=True),
            sender="agent_executor",
            session_id=aworld_context.session_id,
            headers={"context": aworld_context}
        )
        await send_message(msg)
