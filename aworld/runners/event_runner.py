# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import traceback
from typing import List

from aworld.core.common import TaskItem
from aworld.core.context.base import Context

from aworld.core.agent.llm_agent import Agent
from aworld.core.event.base import Message, Constants
from aworld.core.task import Task
from aworld.events.manager import EventManager
from aworld.logs.util import logger
from aworld.runners.handler.base import DefaultHandler

from aworld.runners.task_runner import TaskRunner
from aworld.utils.common import override_in_subclass


class TaskType:
    START = "__start"
    FINISHED = "__finished"
    OUTPUT = "__output"
    ERROR = "__error"
    RERUN = "__rerun"
    # for dynamic subscribe
    SUBSCRIBE_TOOL = "__subscribe_tool"
    SUBSCRIBE_AGENT = "__subscribe_agent"


class TaskEventRunner(TaskRunner):
    __metaclass__ = abc.ABCMeta

    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self._task_response = None
        self.event_mng = EventManager()

    async def pre_run(self):
        await super().pre_run()

        observation = self.observation
        if not observation:
            raise RuntimeError("no observation, check run process")

        # build the first message
        self.init_message = Message(payload=observation,
                                    sender='runner',
                                    receiver=self.swarm.communicate_agent.name(),
                                    session_id=self.context.session_id,
                                    category=Constants.AGENT)

        # register handler
        for key, tool in self.tools.items():
            handlers = self.event_mng.event_bus.get_topic_handlers(Constants.TOOL, tool.name())
            if not handlers:
                await self.event_mng.register(Constants.TOOL, Constants.TOOL, tool.step)
                await self.event_mng.register(Constants.TOOL, tool.name(), tool.step)

        self._stopped = asyncio.Event()

    async def _common_process(self, message: Message) -> List[Message]:
        event_bus = self.event_mng.event_bus

        key = message.category
        transformer = event_bus.get_transform_handlers(key)
        if transformer:
            message = await event_bus.transform(message, handler=transformer)

        results = []
        handlers = event_bus.get_handlers(key)
        if handlers:
            if message.topic:
                handlers = {message.topic: handlers.get(message.topic, [])}
            elif message.receiver:
                handlers = {message.receiver: handlers.get(message.receiver, [])}

        for topic, handler_list in handlers.items():
            if not handler_list:
                logger.warning(f"{topic} no handler, ignore.")
                continue

            con = message.payload
            for handler in handler_list:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        con = await handler(con)
                    else:
                        con = handler(con)

                    if isinstance(con, Message):
                        results.append(con)
                        con = message
                except Exception as e:
                    logger.warning(f"{handler} process fail. {traceback.format_exc()}")

                    await event_bus.publish(Message(
                        category=Constants.TASK,
                        payload=TaskItem(msg=str(e), data=message),
                        sender=self.name,
                        session_id=Context.instance().session_id,
                        topic=TaskType.ERROR
                    ))

        # not handler, return raw message
        if not handlers:
            results.append(message)
        return results

    async def _inner_handler_process(self, results: List[Message], handlers: List[DefaultHandler]):
        # can use runtime backend to parallel
        for handler in handlers:
            for result in results:
                async for event in handler.handle(result):
                    yield event

    @abc.abstractmethod
    async def _do_run(self):
        """"""

    async def do_run(self, context: Context = None):
        if not self.swarm.initialized:
            raise RuntimeError("swarm needs to use `reset` to init first.")

        await self.event_mng.emit_message(self.init_message)
        await self._do_run()
        return self._task_response

    async def stop(self):
        self._stopped.set()

    async def is_stopped(self):
        return self._stopped.is_set()

    def response(self):
        return self._task_response
