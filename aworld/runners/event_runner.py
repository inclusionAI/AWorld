# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import time
import traceback

import aworld.trace as trace

from functools import partial
from typing import List, Callable, Any

from aworld.agents.llm_agent import Agent
from aworld.core.agent.base import BaseAgent
from aworld.core.common import TaskItem, ActionModel
from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants, TopicType, ToolMessage, AgentMessage
from aworld.core.exceptions import AWorldRuntimeException
from aworld.core.task import Task, TaskResponse
from aworld.dataset.trajectory_dataset import generate_trajectory
from aworld.events.manager import EventManager
from aworld.logs.util import logger
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.task_runner import TaskRunner
from aworld.runners.state_manager import EventRuntimeStateManager
from aworld.trace.base import get_trace_id
from aworld.utils.common import override_in_subclass, new_instance


class TaskEventRunner(TaskRunner):
    """Event driven task runner."""

    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self._task_response = None
        self.event_mng = EventManager(self.context)
        self.hooks = {}
        self.handlers = []
        self.init_messages = []
        self.background_tasks = set()
        self.state_manager = EventRuntimeStateManager.instance()

    async def do_run(self, context: Context = None):
        if self.swarm and not self.swarm.initialized:
            raise AWorldRuntimeException("swarm needs to use `reset` to init first.")
        if not self.init_messages:
            raise AWorldRuntimeException("no question event to solve.")

        async with trace.task_span(self.init_messages[0].session_id, self.task):
            try:
                for msg in self.init_messages:
                    await self.event_mng.emit_message(msg)
                await self._do_run()
                await self._save_trajectories()
                resp = self._response()
                logger.info(f'{"sub" if self.task.is_sub_task else "main"} task {self.task.id} finished'
                            f', time cost: {time.time() - self.start_time}s, token cost: {self.context.token_usage}.')
                return resp
            finally:
                # the last step mark output finished
                if not self.task.is_sub_task:
                    logger.info(f'main task {self.task.id} will mark outputs finished')
                    await self.task.outputs.mark_completed()

    async def pre_run(self):
        logger.debug(f"task {self.task.id} pre run start...")
        await super().pre_run()
        self.event_mng.context = self.context
        self.context.event_manager = self.event_mng

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

        self.task_flag = "sub" if self.task.is_sub_task else "main"
        logger.debug(f"{self.task_flag} task: {self.task.id} pre run finish, will start to run...")

    def _build_first_message(self):
        # build the first message
        if self.agent_oriented:
            agents = self.swarm.communicate_agent
            if isinstance(agents, BaseAgent):
                agents = [agents]

            for agent in agents:
                self.init_messages.append(AgentMessage(payload=self.observation,
                                                       sender='runner',
                                                       receiver=agent.id(),
                                                       session_id=self.context.session_id,
                                                       headers={'context': self.context}))
        else:
            actions: List[ActionModel] = self.observation.content
            action_dict = {}
            for action in actions:
                if action.tool_name not in action_dict:
                    action_dict[action.tool_name] = []
                action_dict[action.tool_name].append(action)

            for tool_name, actions in action_dict.items():
                self.init_messages.append(ToolMessage(payload=actions,
                                                      sender='runner',
                                                      receiver=tool_name,
                                                      session_id=self.context.session_id,
                                                      headers={'context': self.context}))

    async def _common_process(self, message: Message) -> List[Message]:
        logger.debug(f"will process message id: {message.id} of task {self.task.id}")
        event_bus = self.event_mng.event_bus

        key = message.category
        logger.info(f"Task {self.task.id} consume message: {message}")
        if key == Constants.TOOL_CALLBACK:
            logger.info(f"Task {self.task.id} Tool callback message {message.id}")
        transformer = self.event_mng.get_transform_handler(key)
        if transformer:
            message = await event_bus.transform(message, handler=transformer)

        results = []
        handlers = self.event_mng.get_handlers(key)
        inner_handlers = [handler.name() for handler in self.handlers]
        async with trace.message_span(message=message):
            logger.debug(f"start_message_node message id: {message.id} of task {self.task.id}")
            self.state_manager.start_message_node(message)
            logger.debug(f"start_message_node end message id: {message.id} of task {self.task.id}")
            if handlers:
                if message.topic:
                    handlers = {message.topic: handlers.get(message.topic, [])}
                elif message.receiver:
                    handlers = {message.receiver: handlers.get(
                        message.receiver, [])}
                else:
                    logger.warning(f"{message.id} no receiver and topic, be ignored.")
                    handlers.clear()

                handle_map = {}
                for topic, handler_list in handlers.items():
                    if not handler_list:
                        logger.warning(f"{topic} no handler, ignore.")
                        continue

                    for handler in handler_list:
                        t = asyncio.create_task(self._handle_task(message, handler))
                        self.background_tasks.add(t)
                        handle_map[t] = False
                    for t, _ in handle_map.items():
                        t.add_done_callback(partial(self._task_done_callback, group=handle_map, message=message))
                        await asyncio.sleep(0)
            if not handlers or message.receiver in inner_handlers:
                # not handler, return raw message
                # if key == Constants.OUTPUT:
                #     return results

                results.append(message)
                t = asyncio.create_task(self._raw_task(results))
                self.background_tasks.add(t)
                t.add_done_callback(partial(self._task_done_callback, message=message))
                await asyncio.sleep(0)
            logger.debug(f"process finished message id: {message.id} of task {self.task.id}")
            return results

    def _task_done_callback(self, task, message: Message, group: dict = None):
        self.background_tasks.discard(task)
        if not group:
            self.state_manager.end_message_node(message)
        else:
            group[task] = True
            if all([v for _, v in group.items()]):
                self.state_manager.end_message_node(message)

    async def _handle_task(self, message: Message, handler: Callable[..., Any]):
        con = message
        async with trace.handler_span(message=message, handler=handler):
            try:
                logger.info(f"process start message id: {message.id} of task {self.task.id}")
                if asyncio.iscoroutinefunction(handler):
                    con = await handler(con)
                else:
                    con = handler(con)

                logger.info(f"process end message id: {message.id} of task {self.task.id}")
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
                logger.warning(f"{handler} process fail. {traceback.format_exc()}")
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
        """Task execution process in real."""
        task_flag = self.task_flag
        start = time.time()
        msg = None
        answer = None
        message = None
        try:
            while True:
                if 0 < self.task.timeout < time.time() - self.start_time:
                    logger.warn(
                        f"{task_flag} task {self.task.id} timeout after {time.time() - self.start_time} seconds.")
                    self._task_response = TaskResponse(answer='',
                                                       success=False,
                                                       context=message.context,
                                                       id=self.task.id,
                                                       time_cost=(time.time() - self.start_time),

                                                       usage=self.context.token_usage,
                                                       msg='cancellation: task timeout',
                                                       status='cancelled')
                    await self.stop()
                if await self.is_stopped():
                    logger.info(f"{task_flag} task {self.task.id} stoped and will break snap")
                    await self.event_mng.done()
                    if self._task_response is None:
                        # send msg to output
                        self._task_response = TaskResponse(msg=msg,
                                                           answer=answer,
                                                           context=message.context,
                                                           success=True if not msg else False,
                                                           id=self.task.id,
                                                           time_cost=(
                                                                   time.time() - start),
                                                           usage=self.context.token_usage,
                                                           status='success' if not msg else 'failed')
                    break
                logger.debug(f"{task_flag} task {self.task.id} next message snap")
                # consume message
                message: Message = await self.event_mng.consume()
                logger.debug(
                    f"consume message {message} of {task_flag} task: {self.task.id}, {self.event_mng.event_bus}")
                # use registered handler to process message
                await self._common_process(message)
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
            if await self.is_stopped():
                try:
                    await self.context.update_task_after_run(self._task_response)
                except:
                    logger.warning("context update_task_after_run fail.")

                if self.swarm and self.swarm.agents:
                    for agent_name, agent in self.swarm.agents.items():
                        try:
                            if hasattr(agent, 'sandbox') and agent.sandbox:
                                await agent.sandbox.cleanup()
                        except Exception as e:
                            logger.warning(f"Failed to cleanup sandbox for agent {agent_name}: {e}")

    async def stop(self):
        self._stopped.set()

    async def is_stopped(self):
        return self._stopped.is_set()

    def response(self):
        return self._task_response

    def _response(self):
        if self.context.get_task().conf and self.context.get_task().conf.resp_carry_context == False:
            self._task_response.context = None
        if self._task_response is None:
            self._task_response = TaskResponse(id=self.context.task_id if self.context else "",
                                               success=False,
                                               msg="Task return None.")
        if self.context.get_task().conf and self.context.get_task().conf.resp_carry_raw_llm_resp == True:
            self._task_response.raw_llm_resp = self.context.context_info.get('llm_output')
        self._task_response.trace_id = get_trace_id()
        return self._task_response

    async def _save_trajectories(self):
        try:
            messages = await self.event_mng.messages_by_task_id(self.task.id)
            trajectory = await generate_trajectory(messages, self.task.id, self.state_manager)
            self._task_response.trajectory = trajectory
        except Exception as e:
            logger.error(f"Failed to get trajectories: {str(e)}.{traceback.format_exc()}")
