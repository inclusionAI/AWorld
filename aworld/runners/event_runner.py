# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import os
import time
import traceback

import aworld.trace as trace
from typing import List, Callable, Any

from aworld.core.common import TaskItem
from aworld.core.context.base import Context

from aworld.agents.llm_agent import Agent
from aworld.core.event.base import Message, Constants, TopicType, ToolMessage, AgentMessage
from aworld.core.task import Task, TaskResponse
from aworld.events.manager import EventManager
from aworld.logs.util import logger
from aworld.replay_buffer import EventReplayBuffer
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler

from aworld.runners.task_runner import TaskRunner
from aworld.utils.common import override_in_subclass, new_instance
from aworld.runners.state_manager import EventRuntimeStateManager


class TaskEventRunner(TaskRunner):
    """Event driven task runner."""

    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self._task_response = None
        self.event_mng = EventManager(self.context)
        self.context.event_manager = self.event_mng
        self.hooks = {}
        self.handlers = []
        self.background_tasks = set()
        self.state_manager = EventRuntimeStateManager.instance()
        self.replay_buffer = EventReplayBuffer()

    async def pre_run(self):
        logger.debug(f"[TaskEventRunner] pre_run start {self.task.id}")
        await super().pre_run()

        if self.swarm and not self.swarm.max_steps:
            self.swarm.max_steps = self.task.conf.get('max_steps', 10)
        observation = self.observation
        if not observation:
            raise RuntimeError("no observation, check run process")

        self._build_first_message()

        if self.swarm:
            logger.debug(f"swarm: {self.swarm}")
            # register agent handler
            for _, agent in self.swarm.agents.items():
                if override_in_subclass('async_policy', agent.__class__, Agent):
                    await self.event_mng.register(Constants.AGENT, agent.id(), agent.async_run)
                else:
                    await self.event_mng.register(Constants.AGENT, agent.id(), agent.run)
        # register tool handler
        for key, tool in self.tools.items():
            if tool.handler:
                await self.event_mng.register(Constants.TOOL, tool.name(), tool.handler)
            else:
                await self.event_mng.register(Constants.TOOL, tool.name(), tool.step)
            handlers = self.event_mng.event_bus.get_topic_handlers(
                Constants.TOOL, tool.name())
            if not handlers:
                await self.event_mng.register(Constants.TOOL, Constants.TOOL, tool.step)

        self._stopped = asyncio.Event()

        # handler of process in framework
        handler_list = self.conf.get("handlers")
        if handler_list:
            # handler class name
            for hand in handler_list:
                self.handlers.append(new_instance(hand, self))
        else:
            for handler in HandlerFactory:
                self.handlers.append(HandlerFactory(handler, runner=self))
        logger.debug(f"[TaskEventRunner] pre_run finish {self.task.id}")

    def _build_first_message(self):
        # build the first message
        if self.agent_oriented:
            self.init_message = AgentMessage(payload=self.observation,
                                             sender='runner',
                                             receiver=self.swarm.communicate_agent.id(),
                                             session_id=self.context.session_id,
                                             headers={'context': self.context})
        else:
            actions = self.observation.content
            receiver = actions[0].tool_name
            self.init_message = ToolMessage(payload=self.observation.content,
                                            sender='runner',
                                            receiver=receiver,
                                            session_id=self.context.session_id,
                                            headers={'context': self.context})

    async def _common_process(self, message: Message) -> List[Message]:
        logger.debug(
            f"[TaskEventRunner] _common_process start {self.task.id}, message_id = {message.id}")
        event_bus = self.event_mng.event_bus

        key = message.category
        transformer = self.event_mng.get_transform_handler(key)
        if transformer:
            message = await event_bus.transform(message, handler=transformer)

        results = []
        handlers = self.event_mng.get_handlers(key)
        async with trace.message_span(message=message):
            logger.debug(
                f"[TaskEventRunner] start_message_node start {self.task.id}, message_id = {message.id}")
            self.state_manager.start_message_node(message)
            logger.debug(
                f"[TaskEventRunner] start_message_node end {self.task.id}, message_id = {message.id}")
            if handlers:
                if message.topic:
                    handlers = {message.topic: handlers.get(message.topic, [])}
                elif message.receiver:
                    handlers = {message.receiver: handlers.get(
                        message.receiver, [])}
                else:
                    logger.warning(
                        f"{message.id} no receiver and topic, be ignored.")
                    handlers.clear()

                handle_tasks = []
                for topic, handler_list in handlers.items():
                    if not handler_list:
                        logger.warning(f"{topic} no handler, ignore.")
                        continue

                    for handler in handler_list:
                        t = asyncio.create_task(
                            self._handle_task(message, handler))
                        handle_tasks.append(t)
                logger.debug(
                    f"[TaskEventRunner] _common_process handle_tasks collect finished {self.task.id}, message_id = {message.id}")

                # For _handle_task case, end message node asynchronously
                async def async_end_message_node():
                    logger.debug(
                        f"[TaskEventRunner] async_end_message_node STARTED {self.task.id}, message_id = {message.id}")
                    try:
                        # Wait for all _handle_task tasks to complete before ending message node
                        if handle_tasks:
                            logger.debug(
                                f"[TaskEventRunner] async_end_message_node {self.task.id} Before gather {len(handle_tasks)} tasks")
                            await asyncio.gather(*handle_tasks)
                            logger.debug(
                                f"[TaskEventRunner] async_end_message_node {self.task.id} After gather tasks completed")
                        logger.debug(
                            f"[TaskEventRunner] _common_process handle_tasks process end_message_node start {self.task.id}, message_id = {message.id}")
                        self.state_manager.end_message_node(message)
                        logger.debug(
                            f"[TaskEventRunner] _common_process handle_tasks process finished {self.task.id}, message_id = {message.id}")
                    except Exception as e:
                        logger.error(f"Error in async_end_message_node: {e}")
                        raise

                end_node_task = asyncio.create_task(async_end_message_node())
                self.background_tasks.add(end_node_task)
                end_node_task.add_done_callback(self.background_tasks.discard)
            else:
                # not handler, return raw message
                results.append(message)

                t = asyncio.create_task(self._raw_task(results))
                self.background_tasks.add(t)
                t.add_done_callback(self.background_tasks.discard)
                # wait until it is complete
                await t
                self.state_manager.end_message_node(message)
            logger.debug(
                f"[TaskEventRunner] _common_process return results {self.task.id}, message_id = {message.id},  ")
            return results

    async def _handle_task(self, message: Message, handler: Callable[..., Any]):
        con = message
        async with trace.handler_span(message=message, handler=handler):
            try:
                logger.debug(
                    f"event_runner _handle_task - self: {self}, swarm: {self.swarm}, event_mng: {self.event_mng}, event_bus: {self.event_mng.event_bus}, message: {message}")
                logger.info(
                    f"[TaskEventRunner] {self.task.id} _handle_task start, message: {message.id}")
                if asyncio.iscoroutinefunction(handler):
                    con = await handler(con)
                else:
                    con = handler(con)

                logger.info(
                    f"[TaskEventRunner] {self.task.id} _handle_task  finished message= {message.id}, session_id = {self.task.session_id}")
                if isinstance(con, Message):
                    # process in framework
                    self.state_manager.save_message_handle_result(name=handler.__name__,
                                                                  message=message,
                                                                  result=con)
                    async for event in self._inner_handler_process(
                            results=[con],
                            handlers=self.handlers
                    ):
                        await self.event_mng.emit_message(event)
                else:
                    self.state_manager.save_message_handle_result(name=handler.__name__,
                                                                  message=message)
            except Exception as e:
                logger.warning(
                    f"{handler} process fail. {traceback.format_exc()}")
                error_msg = Message(
                    category=Constants.TASK,
                    payload=TaskItem(msg=str(e), data=message),
                    sender=self.name,
                    session_id=self.context.session_id,
                    topic=TopicType.ERROR,
                    headers={"context": self.context}
                )
                self.state_manager.save_message_handle_result(name=handler.__name__,
                                                              message=message,
                                                              result=error_msg)
                await self.event_mng.emit_message(error_msg)

    async def _raw_task(self, messages: List[Message]):
        # process in framework
        async for event in self._inner_handler_process(
                results=messages,
                handlers=self.handlers
        ):
            await self.event_mng.emit_message(event)

    async def _inner_handler_process(self, results: List[Message], handlers: List[DefaultHandler]):
        # can use runtime backend to parallel
        for handler in handlers:
            for result in results:
                async for event in handler.handle(result):
                    yield event

    async def _do_run(self):
        logger.debug(f"[TaskEventRunner] _do_run start {self.task.id}")

        """Task execution process in real."""
        start = time.time()
        msg = None
        answer = None
        message = None
        try:
            while True:
                if await self.is_stopped():
                    logger.debug(
                        f"[TaskEventRunner] break snap {self.task.id}")
                    await self.event_mng.done()
                    logger.info(
                        f" [TaskEventRunner] stop task {self.task.id}...")
                    if self._task_response is None:
                        # send msg to output
                        self._task_response = TaskResponse(msg=msg,
                                                           answer=answer,
                                                           context=message.context,
                                                           success=True if not msg else False,
                                                           id=self.task.id,
                                                           time_cost=(
                                                               time.time() - start),
                                                           usage=self.context.token_usage)
                    break
                logger.debug(f"[TaskEventRunner] next snap {self.task.id}")
                # consume message
                message: Message = await self.event_mng.consume()
                logger.debug(
                    f"[TaskEventRunner] next consume finished {self.task.id}, event_bus: {self.event_mng.event_bus},: message = {message}")
                # use registered handler to process message
                await self._common_process(message)
                logger.debug(
                    f"[TaskEventRunner] _common_process finished {self.task.id}")
        except Exception as e:
            logger.error(f"consume message fail. {traceback.format_exc()}")
            error_msg = Message(
                category=Constants.TASK,
                payload=TaskItem(msg=str(e), data=message),
                sender=self.name,
                session_id=self.context.session_id,
                topic=TopicType.ERROR,
                headers={"context": self.context}
            )
            self.state_manager.save_message_handle_result(name=TaskEventRunner.__name__,
                                                          message=message,
                                                          result=error_msg)
            await self.event_mng.emit_message(error_msg)
        finally:
            logger.debug(
                f"[TaskEventRunner] _do_run finished  await_is_stopped {self.task.id}")
            if await self.is_stopped():
                logger.info(
                    f"[TaskEventRunner] _do_run finished is_stopped {self.task.id}")
                if not self.task.is_sub_task:
                    logger.info(f"FINISHED|TaskEventRunner|outputs|{self.task.id} {self.task.is_sub_task}")
                    await self.task.outputs.mark_completed()

                if self.swarm and self.swarm.agents:
                    for agent_name, agent in self.swarm.agents.items():
                        try:
                            if hasattr(agent, 'sandbox') and agent.sandbox:
                                await agent.sandbox.cleanup()
                        except Exception as e:
                            logger.warning(
                                f"event_runner Failed to cleanup sandbox for agent {agent_name}: {e}")

    async def do_run(self, context: Context = None):
        if self.swarm and not self.swarm.initialized:
            raise RuntimeError("swarm needs to use `reset` to init first.")
        async with trace.task_span(self.init_message.session_id, self.task):
            await self.event_mng.emit_message(self.init_message)
            await self._do_run()
            await self._save_trajectories()
            return self._task_response

    async def stop(self):
        self._stopped.set()

    async def is_stopped(self):
        return self._stopped.is_set()

    def response(self):
        return self._task_response

    async def _save_trajectories(self):
        try:
            messages = self.event_mng.messages_by_task_id(self.task.id)
            trajectory = await self.replay_buffer.get_trajectory(messages, self.task.id)
            self._task_response.trajectory = trajectory
        except Exception as e:
            logger.error(f"Failed to get trajectories: {str(e)}.{traceback.format_exc()}")
