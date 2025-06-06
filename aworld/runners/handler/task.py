# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import time

from typing import AsyncGenerator

from aworld.core.common import TaskItem
from aworld.core.tool.base import Tool, AsyncTool

from aworld.core.event.base import Message, Constants
from aworld.core.task import TaskResponse
from aworld.logs.util import logger
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import HookPoint
from aworld.runners.utils import TaskType


class TaskHandler(DefaultHandler):
    __metaclass__ = abc.ABCMeta

    def __init__(self, runner: 'TaskEventRunner'):
        self.runner = runner

    @classmethod
    def name(cls):
        return "_task_handler"


class DefaultTaskHandler(TaskHandler):
    async def handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if message.category != Constants.TASK:
            return

        topic = message.topic
        task_item: TaskItem = message.payload
        if topic == TaskType.SUBSCRIBE_TOOL:
            new_tools = message.payload.data
            for name, tool in new_tools.items():
                if isinstance(tool, Tool) or isinstance(tool, AsyncTool):
                    await self.runner.event_mng.register(Constants.TOOL, name, tool.step)
                    logger.info(f"dynamic register {name} tool.")
                else:
                    logger.warning(f"Unknown tool instance: {tool}")
            return
        elif topic == TaskType.SUBSCRIBE_AGENT:
            return
        elif topic == TaskType.ERROR:
            async for event in self.run_hooks(message, HookPoint.ERROR):
                yield event

            if task_item.stop:
                await self.runner.stop()
                logger.warning(f"task {self.runner.task.id} stop, cause: {task_item.msg}")
                self.runner._task_response = TaskResponse(msg=task_item.msg,
                                                          answer='',
                                                          success=False,
                                                          id=self.runner.task.id,
                                                          time_cost=(time.time() - self.runner.start_time),
                                                          usage=self.runner.context.token_usage)
                return
            # restart
            logger.warning(f"The task {self.runner.task.id} will be restarted due to error: {task_item.msg}.")
            yield Message(
                category=Constants.TASK,
                payload='',
                sender=self.name(),
                session_id=self.runner.context.session_id,
                topic=TaskType.START
            )
        elif topic == TaskType.FINISHED:
            async for event in self.run_hooks(message, HookPoint.FINISHED):
                yield event

            self.runner._task_response = TaskResponse(answer=str(message.payload),
                                                      success=True,
                                                      id=self.runner.task.id,
                                                      time_cost=(time.time() - self.runner.start_time),
                                                      usage=self.runner.context.token_usage)
            await self.runner.stop()

            logger.info(f"{self.runner.task.id} finished.")
            yield Message(
                category=Constants.TASK,
                payload='',
                sender=self.name(),
                session_id=self.runner.context.session_id,
                topic=TaskType.FINISHED
            )
        elif topic == TaskType.START:
            async for event in self.run_hooks(message, HookPoint.START):
                yield event

            logger.info(f"task start event: {message}, will send init message.")
            if message.payload:
                yield message
            else:
                yield self.runner.init_message
        elif topic == TaskType.OUTPUT:
            yield message

    async def run_hooks(self, message: Message, hook_point: str) -> AsyncGenerator[Message, None]:
        hooks = HookFactory.hooks(hook_point).get(hook_point)
        for hook in hooks:
            try:
                msg = hook(message)
                if msg:
                    yield msg
            except:
                logger.warning(f"{hook.point()} {hook.name()} execute fail.")
