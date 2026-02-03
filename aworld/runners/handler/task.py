# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import json
import time
import traceback
from typing import AsyncGenerator, TYPE_CHECKING

from aworld.core.common import TaskItem
from aworld.core.event.base import Message, Constants, TopicType
from aworld.core.task import TaskResponse, TaskStatusValue
from aworld.core.tool.base import Tool, AsyncTool
from aworld.logs.util import logger, trajectory_logger
from aworld.output import Output
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import HookPoint
from aworld.utils.serialized_util import to_serializable

if TYPE_CHECKING:
    from aworld.runners.event_runner import TaskEventRunner


class TaskHandler(DefaultHandler):
    __metaclass__ = abc.ABCMeta

    def __init__(self, runner: 'TaskEventRunner'):
        super().__init__(runner)
        self.runner = runner
        self.retry_count = runner.task.max_retry_count
        self.hooks = {}
        if runner.task.hooks:
            for k, vals in runner.task.hooks.items():
                self.hooks[k] = []
                for v in vals:
                    cls = HookFactory.get_class(v)
                    if cls:
                        self.hooks[k].append(cls)

    @classmethod
    def name(cls):
        return "_task_handler"


@HandlerFactory.register(name=f'__{Constants.TASK}__')
class DefaultTaskHandler(TaskHandler):
    def is_valid_message(self, message: Message):
        if message.category != Constants.TASK:
            return False
        return True

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        task_flag = "sub" if self.runner.task.is_sub_task else "main"
        logger.debug(f"task handler receive message: {message}")

        headers = {"context": message.context}
        self.runner.context.merge_context(message.context)
        topic = message.topic
        task_item: TaskItem = message.payload
        if topic == TopicType.SUBSCRIBE_TOOL:
            new_tools = message.payload.data
            for name, tool in new_tools.items():
                try:
                    if isinstance(tool, Tool) or isinstance(tool, AsyncTool):
                        # Prioritize using handlers
                        if tool.handler:
                            await self.runner.event_mng.register(Constants.TOOL, name, tool.handler)
                        else:
                            await self.runner.event_mng.register(Constants.TOOL, name, tool.step)
                        logger.info(f"Task {self.runner.task.id} dynamic register {name} tool.")
                    else:
                        logger.warning(f"Task {self.runner.task.id}#Unknown tool instance: {tool}")
                except Exception as e:
                    logger.warn(f"Task {self.runner.task.id}#Failed to register new tool {name}: {str(e)}. {traceback.format_exc()}")
            return
        elif topic == TopicType.SUBSCRIBE_AGENT:
            return
        elif topic == TopicType.ERROR:
            async for event in self.run_hooks(message, HookPoint.ERROR):
                yield event

            logger.warning(f"{task_flag} task {self.runner.task.id} stop, cause: {task_item.msg}")
            self.runner._task_response = TaskResponse(msg=task_item.msg,
                                                      answer=f'Task fail, cause: {task_item.msg}',
                                                      context=message.context,
                                                      success=False,
                                                      id=self.runner.task.id,
                                                      time_cost=(time.time() - self.runner.start_time),
                                                      usage=self.runner.context.token_usage,
                                                      status=TaskStatusValue.FAILED)
            await self.runner.stop()
            yield Message(payload=self.runner._task_response,
                          session_id=message.session_id,
                          headers=message.headers,
                          topic=TopicType.TASK_RESPONSE)
        elif topic == TopicType.FINISHED:
            async for event in self.run_hooks(message, HookPoint.FINISHED):
                yield event

            status = "running" if message.headers.get("step_interrupt", False) else "finished"
            self.runner._task_response = TaskResponse(answer=message.payload,
                                                      success=True,
                                                      context=message.context,
                                                      id=self.runner.task.id,
                                                      time_cost=(time.time() - self.runner.start_time),
                                                      usage=self.runner.context.token_usage,
                                                      status=status)

            logger.info(f"{task_flag} task {self.runner.task.id} receive finished message.")

            await self.runner.stop()
            yield Message(payload=self.runner._task_response, session_id=message.session_id, headers=message.headers,
                          topic=TopicType.TASK_RESPONSE)
        elif topic == TopicType.START:
            async for event in self.run_hooks(message, HookPoint.START):
                yield event

            logger.info(f"{task_flag} task start event: {message}, will send init message.")
            if message.payload:
                yield message
            else:
                for msg in self.runner.init_messages:
                    yield msg
        elif topic == TopicType.OUTPUT:
            yield message
        elif topic == TopicType.HUMAN_CONFIRM:
            logger.warn("=============== Get human confirm, pause execution ===============")
            if self.runner.task.outputs and message.payload:
                await self.runner.task.outputs.add_output(Output(data=message.payload))
            self.runner._task_response = TaskResponse(answer=message.payload,
                                                      success=True,
                                                      context=message.context,
                                                      id=self.runner.task.id,
                                                      time_cost=(time.time() - self.runner.start_time),
                                                      usage=self.runner.context.token_usage)
            await self.runner.stop()
            yield Message(payload=self.runner._task_response, session_id=message.session_id, headers=message.headers,
                          topic=TopicType.TASK_RESPONSE)
        elif topic == TopicType.CANCEL:
            # Avoid waiting to receive events and send a mock event for quick cancel
            yield Message(session_id=self.runner.context.session_id, sender=self.name(), category='mock',
                          headers={"context": message.context})
            # mark task response as cancelled
            self.runner._task_response = TaskResponse(answer='',
                                                      success=False,
                                                      context=message.context,
                                                      id=self.runner.task.id,
                                                      time_cost=(time.time() - self.runner.start_time),
                                                      usage=self.runner.context.token_usage,
                                                      msg=f'cancellation message received: {task_item.msg}',
                                                      status=TaskStatusValue.CANCELLED)
            await self.runner.stop()
            yield Message(payload=self.runner._task_response, session_id=message.session_id, headers=message.headers,
                          topic=TopicType.TASK_RESPONSE)
        elif topic == TopicType.INTERRUPT:
            # Avoid waiting to receive events and send a mock event for quick interrupt
            yield Message(session_id=self.runner.context.session_id, sender=self.name(), category='mock',
                          headers={"context": message.context})
            # mark task response as interrupted
            self.runner._task_response = TaskResponse(answer='',
                                                      success=False,
                                                      context=message.context,
                                                      id=self.runner.task.id,
                                                      time_cost=(time.time() - self.runner.start_time),
                                                      usage=self.runner.context.token_usage,
                                                      msg=f'interruption message received: {task_item.msg}',
                                                      status=TaskStatusValue.INTERRUPTED)
            await self.runner.stop()
            yield Message(payload=self.runner._task_response, session_id=message.session_id, headers=message.headers,
                          topic=TopicType.TASK_RESPONSE)
