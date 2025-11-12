# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Union

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TextPart, TaskStatusUpdateEvent, TaskStatus, TaskState, TaskArtifactUpdateEvent
from a2a.utils import new_task, new_text_artifact, new_agent_text_message, Task as A2ATask

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import TaskItem
from aworld.core.context.base import Context
from aworld.core.event.base import CancelMessage
from aworld.core.task import Task
from aworld.events.util import send_message
from aworld.logs.util import logger
from aworld.utils.run_util import exec_tasks
from aworld.runner import Runners
from aworld.output.utils import consume_content
from aworld.output.base import MessageOutput, ToolResultOutput, StepOutput, Output


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
            async for output in Runners.streamed_run_task(aworld_task).stream_events():
                logger.info(f"task: {aworld_task.id} execute streaming. {output}")

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

    async def _stream_output_process(self, output: Output, event_queue: EventQueue, task: A2ATask):
        event = None
        if isinstance(output, MessageOutput):
            event = await self._message_output(output)
        elif isinstance(output, ToolResultOutput):
            event = await self._tool_result(output)
        elif isinstance(output, StepOutput):
            event = await self._step(output)

        if event and event['status'] == 'completed':
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    append=True,
                    context_id=task.context_id,
                    task_id=task.id,
                    artifact=new_text_artifact(
                        name='current_result',
                        description='Result of request to agent.',
                        text=event['content'],
                    ),
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(state=TaskState.completed),
                    final=True,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )
        elif event and event['status'] == 'failed':
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(state=TaskState.failed),
                    final=True,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )
        else:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    append=True,
                    status=TaskStatus(
                        state=TaskState.working,
                        message=new_agent_text_message(
                            event['content'],
                            task.context_id,
                            task.id,
                        ),
                    ),
                    final=False,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )

    async def _message_output(self, __output__: MessageOutput):
        reason_result = []
        response_result = []

        async def _reason_call_back(item):
            reason_result.append(item)

        async def _response_call_back(item):
            response_result.append(item)

        if __output__.reason_generator:
            await consume_content(__output__.reason_generator, _reason_call_back)
            return {"status": "working", "content": reason_result}
        elif __output__.reasoning:
            await consume_content(__output__.reasoning, _reason_call_back)
            return {"status": "working", "content": reason_result}

        if __output__.response_generator:
            await consume_content(__output__.response_generator, _response_call_back)
            return {"status": "working", "content": response_result}
        else:
            await consume_content(__output__.response, _response_call_back)
            return {"status": "working", "content": response_result}

    async def _tool_result(self, output: ToolResultOutput):
        """
            tool_result
        """
        return {"status": "working", "content": f"tool_call_function: {output.origin_tool_call.function.name}, tool_call_arguments: {output.origin_tool_call.function.arguments}, tool_result: {output.data}"}

    async def _step(self, output: StepOutput):
        if output.status == "START":
            return {"status": "working", "content": f"[bold green]{output.name} ✈️START ..."}
        elif output.status == "FINISHED":
            return {"status": "completed", "content": f"[bold green]{output.name} 🛬FINISHED ..."}
        elif output.status == "FAILED":
            return {"status": "failed", "content": f"[bold red]{output.name} 💥FAILED ..."}
        else:
            self.status.stop()
            self.console.print(f"============={output.name} ❓❓❓UNKNOWN#{output.status} ======================")
