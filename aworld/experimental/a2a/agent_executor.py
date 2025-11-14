# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
from typing import Union

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TextPart, TaskState
from a2a.utils import new_task, new_agent_text_message

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import TaskItem, StreamingMode
from aworld.core.context.base import Context
from aworld.core.event.base import CancelMessage, TopicType, Constants
from aworld.core.task import Task
from aworld.events.util import send_message
from aworld.logs.util import logger
from aworld.utils.run_util import exec_tasks, streaming_exec_task
from aworld.utils.serialized_util import to_serializable


class AworldAgentExecutor(AgentExecutor):
    def __init__(self, agent: Union[Agent, Swarm], streaming: bool = False):
        self.agent = agent
        self.streaming = streaming

    def _get_message_meta_str(self, metadata, key: str) -> str:
        if not metadata:
            return None
        return metadata.get(key, None)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if not context.message:
            raise ValueError(f"No message in context {context}")

        query = context.get_user_input()
        task = context.current_task
        if not task:
            task = new_task(context.message)  # type: ignore
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        aworld_task = Task(input=query,
                           agent=self.agent,
                           user_id=self._get_message_meta_str(context.message.metadata, 'user_id'),
                           session_id=self._get_message_meta_str(context.message.metadata, 'session_id'),
                           id=self._get_message_meta_str(context.message.metadata, 'task_id'),
                           conf=self._get_message_meta_str(context.message.metadata, 'task_conf'),
                           )
        run_conf = self._get_message_meta_str(context.message.metadata, 'run_conf')
        streaming_mode = self._get_message_meta_str(context.message.metadata, 'streaming_mode')

        if self.streaming:
            aworld_task.streaming_mode = StreamingMode(streaming_mode) if streaming_mode else StreamingMode.CORE
            async for msg in streaming_exec_task(aworld_task, run_conf):
                if msg.topic == TopicType.TASK_RESPONSE and msg.task_id == aworld_task.id:
                    output_parts = [Part(root=TextPart(text=msg.payload.answer))]
                    await updater.add_artifact(
                        parts=output_parts,
                        name='aworld_artifact',
                    )

                    parts = [Part(root=TextPart(text=msg.payload.answer))]
                    msg = updater.new_agent_message(parts=parts)
                    await updater.complete(msg)
                    break
                else:
                    payload = msg.payload
                    if msg.category == Constants.CHUNK:
                        content = payload.content or payload.tool_calls
                    elif msg.topic == TopicType.TASK_RESPONSE:
                        content = payload.answer
                    elif msg.category == Constants.TOOL:
                        content = json.dumps(to_serializable(payload))
                    elif msg.category == Constants.AGENT:
                        continue
                    else:
                        content = str(payload)

                    content = '' if content is None else content
                    await updater.update_status(
                        state=TaskState.working,
                        message=new_agent_text_message(content, task.context_id, task.id)
                    )
        else:
            resp = await exec_tasks([aworld_task], run_conf)

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
