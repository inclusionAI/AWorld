# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
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
from aworld.runners.callback.tool import ToolCallbackHandler
from aworld.runners.handler.agent import DefaultAgentHandler, AgentHandler
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.handler.output import DefaultOutputHandler
from aworld.runners.handler.task import DefaultTaskHandler, TaskHandler
from aworld.runners.handler.tool import DefaultToolHandler, ToolHandler

from aworld.runners.task_runner import TaskRunner
from aworld.utils.common import override_in_subclass, new_instance
from aworld.runners.state_manager import EventRuntimeStateManager


class TaskEventRunner(TaskRunner):
    """Event driven task runner."""

    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self._task_response = None
        self.event_mng = EventManager(self.context)
        self.hooks = {}
        self.background_tasks = set()
        self.state_manager = EventRuntimeStateManager.instance()

    async def pre_run(self):
        await super().pre_run()

        if self.swarm and not self.swarm.max_steps:
            self.swarm.max_steps = self.task.conf.get('max_steps', 10)
        observation = self.observation
        if not observation:
            raise RuntimeError("no observation, check run process")

        self._build_first_message()

        if self.swarm:
            # register agent handler
            for _, agent in self.swarm.agents.items():
                agent.set_tools_instances(self.tools, self.tools_conf)
                if agent.handler:
                    await self.event_mng.register(Constants.AGENT, agent.id(), agent.handler)
                else:
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
            handlers = []
            for hand in handler_list:
                handlers.append(new_instance(hand, self))

            has_task_handler = False
            has_tool_handler = False
            has_agent_handler = False
            for hand in handlers:
                if isinstance(hand, TaskHandler):
                    has_task_handler = True
                elif isinstance(hand, ToolHandler):
                    has_tool_handler = True
                elif isinstance(hand, AgentHandler):
                    has_agent_handler = True

            if not has_agent_handler:
                self.handlers.append(DefaultAgentHandler(runner=self))
            if not has_tool_handler:
                self.handlers.append(DefaultToolHandler(runner=self))
            if not has_task_handler:
                self.handlers.append(DefaultTaskHandler(runner=self))
            self.handlers = handlers
        else:
            self.handlers = [DefaultAgentHandler(runner=self),
                             DefaultToolHandler(runner=self),
                             DefaultTaskHandler(runner=self),
                             DefaultOutputHandler(runner=self),
                             ToolCallbackHandler(runner=self)
                             ]

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
        event_bus = self.event_mng.event_bus

        key = message.category
        transformer = event_bus.get_transform_handlers(key)
        print(f"_common_process: {message}")
        if transformer:
            message = await event_bus.transform(message, handler=transformer)

        results = []
        handlers = event_bus.get_handlers(key)
        async with trace.message_span(message=message):
            self.state_manager.start_message_node(message)
            if handlers:
                if message.topic:
                    handlers = {message.topic: handlers.get(message.topic, [])}
                elif message.receiver:
                    handlers = {message.receiver: handlers.get(
                        message.receiver, [])}

                handle_tasks = []
                for topic, handler_list in handlers.items():
                    if not handler_list:
                        logger.warning(f"{topic} no handler, ignore.")
                        continue

                    for handler in handler_list:
                        t = asyncio.create_task(
                            self._handle_task(message, handler))
                        handle_tasks.append(t)
                        self.background_tasks.add(t)
                        t.add_done_callback(self.background_tasks.discard)
                
                # For _handle_task case, end message node asynchronously
                async def async_end_message_node():
                    # Wait for all _handle_task tasks to complete before ending message node
                    if handle_tasks:
                        await asyncio.gather(*handle_tasks)
                    self.state_manager.end_message_node(message)
                asyncio.create_task(async_end_message_node())
            else:
                # not handler, return raw message
                results.append(message)

                t = asyncio.create_task(self._raw_task(results))
                self.background_tasks.add(t)
                t.add_done_callback(self.background_tasks.discard)
                # wait until it is complete
                await t
                self.state_manager.end_message_node(message)
            return results

    async def _handle_task(self, message: Message, handler: Callable[..., Any]):
        con = message
        async with trace.span(handler.__name__):
            try:
                logger.info(
                    f"event_runner _handle_task start, message: {message.id}")
                if asyncio.iscoroutinefunction(handler):
                    con = await handler(con)
                else:
                    con = handler(con)

                logger.info(f"event_runner _handle_task message= {message.id}")
                if isinstance(con, Message):
                    # process in framework
                    self.state_manager.save_message_handle_result(name=handler.__name__,
                                                                  message=message,
                                                                  result=con)
                    print(f"con: {con} \n handlers: {self.handlers}")
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
                    session_id=Context.instance().session_id,
                    topic=TopicType.ERROR
                )
                self.state_manager.save_message_handle_result(name=handler.__name__,
                                                              message=message,
                                                              result=error_msg)
                await self.event_mng.event_bus.publish(error_msg)

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
        """Task execution process in real."""
        start = time.time()
        msg = None
        answer = None

        try:
            while True:
                if await self.is_stopped():
                    await self.event_mng.done()
                    logger.info("stop task...")
                    if self._task_response is None:
                        # send msg to output
                        self._task_response = TaskResponse(msg=msg,
                                                           answer=answer,
                                                           success=True if not msg else False,
                                                           id=self.task.id,
                                                           time_cost=(
                                                                   time.time() - start),
                                                           usage=self.context.token_usage)
                    break

                # consume message
                message: Message = await self.event_mng.consume()

                # use registered handler to process message
                await self._common_process(message)
        except Exception as e:
            logger.error(f"consume message fail. {traceback.format_exc()}")
        finally:
            if await self.is_stopped():
                await self.task.outputs.mark_completed()
                # todo sandbox cleanup
                if self.swarm and hasattr(self.swarm, 'agents') and self.swarm.agents:
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
        async with trace.span("Task_" + self.init_message.session_id):
            await self.event_mng.emit_message(self.init_message)
            await self._do_run()
            return self._task_response

    async def stop(self):
        self._stopped.set()

    async def is_stopped(self):
        return self._stopped.is_set()

    def response(self):
        return self._task_response
