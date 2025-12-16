# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import json
import time
import traceback
from functools import partial
from typing import List, Callable, Any

import aworld.trace as trace
from aworld.agents.llm_agent import Agent
from aworld.core.agent.base import BaseAgent, is_agent_by_name
from aworld.core.common import TaskItem, ActionModel
from aworld.core.context.base import Context
from aworld.core.context.trajectory_storage import InMemoryTrajectoryStorage, get_storage_instance
from aworld.core.event.base import Message, Constants, TopicType, ToolMessage, AgentMessage
from aworld.core.exceptions import AWorldRuntimeException
from aworld.core.task import Task, TaskResponse, TaskStatusValue
from aworld.dataset.trajectory_dataset import generate_trajectory_from_strategy, TrajectoryDataset
from aworld.events.manager import EventManager
from aworld.logs.util import logger
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.state_manager import EventRuntimeStateManager
from aworld.runners.task_runner import TaskRunner
from aworld.trace.base import get_trace_id
from aworld.utils.common import override_in_subclass, new_instance
from aworld.utils.serialized_util import to_serializable


class TaskEventRunner(TaskRunner):
    """Event driven task runner."""

    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self._task_response = None
        self.event_mng = EventManager(self.context, streaming_mode=task.streaming_mode)
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

        if self.context.trajectory_dataset is None:
            trajectory_storage = self.conf.get('trajectory_storage', None)
            storage_instance = get_storage_instance(trajectory_storage)
            
            traj_dataset = TrajectoryDataset(
                name=f"{self.task.id}_trajectory_dataset",
                state_manager=self.state_manager,
                storage=storage_instance,
                enable_storage=False,
                data=[],
                strategy=self.conf.get('trajectory_strategy', None)
            )
            self.context.init_trajectory_dataset(traj_dataset)
        if not self.context.task_graph and not self.task.is_sub_task:
            self.context.task_graph = {self.task.id: {'parent_task': None}}

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
                if override_in_subclass('async_policy', agent.__class__, BaseAgent):
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
                handler_instance = HandlerFactory(handler, runner=self)
                self.handlers.append(handler_instance)

        self.task_flag = "sub" if self.task.is_sub_task else "main"
        logger.debug(f"{self.task_flag} task: {self.task.id} pre run finish, will start to run...")

    def _build_first_message(self):
        new_context = self.context.deep_copy()
        new_context._task = self.context.get_task()
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
                                                       headers={'context': new_context}))
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
                                                      headers={'context': new_context}))

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
            asyncio.create_task(self._update_trajectory(message))
            if handlers:
                handler_list = handlers.get(message.topic) or handlers.get(message.receiver)
                if not handler_list:
                    logger.warning(f"{message.topic}/{message.receiver} no handler, ignore.")
                    handlers.clear()
                else:
                    handle_map = {}

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
                # This creates a strong reference, see https://docs.python.org/3/library/asyncio-task.html#id4
                self.background_tasks.add(t)
                t.add_done_callback(partial(self._task_done_callback, message=message))
                await asyncio.sleep(0)
            logger.debug(f"process finished message id: {message.id} of task {self.task.id}")
            return results

    def _task_done_callback(self, task, message: Message, group: dict = None):
        # To prevent keeping references to finished tasks forever, make each task remove its own reference
        # from the set after completion, see https://docs.python.org/3/library/asyncio-task.html#id4
        self.background_tasks.discard(task)
        if not group:
            self.state_manager.end_message_node(message)
            asyncio.create_task(self._update_trajectory(message))
        else:
            group[task] = True
            if all([v for _, v in group.items()]):
                self.state_manager.end_message_node(message)
                asyncio.create_task(self._update_trajectory(message))

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
                if await self.should_stop_task(result):
                    await self.stop()
                    return
                async for event in handler.handle(result):
                    yield event

    async def _update_trajectory(self, message: Message):
        try:
            # valid_agent_messages = await TrajectoryDataset._filter_replay_messages([message], self.task.id)

            if message.context.task_id != self.task.id or message.category != Constants.AGENT:
                return
            sender = message.sender
            receiver = message.receiver
            if not sender or not receiver or not is_agent_by_name(receiver):
                return
            agent_as_tool = message.headers.get("agent_as_tool", False)
            if agent_as_tool:
                return
            await self.context.update_task_trajectory(message, self.task.id)

            # data_row = self.context.trajectory_dataset.message_to_datarow(message)
            # if data_row:
            #     # traj = self.context.trajectories.get(self.task.id, [])
            #     # traj.append(to_serializable(data_row))
            #     # self.trajectory_dataset.data.append(to_serializable(data_row))
            #
            #     row_data = to_serializable(data_row)
            #     await self.context.update_task_trajectory(self.task.id, [row_data])

        except Exception as e:
            logger.warning(f"Failed to update trajectory for message {message.id}: {e}")

    async def _do_run(self):
        """Task execution process in real."""
        task_flag = self.task_flag
        start = time.time()
        msg = None
        answer = None
        message = None

        try:
            while True:
                # External control - Check task status before processing each message
                should_stop_task = await self.should_stop_task(message)
                if should_stop_task:
                    logger.warn(f"Runner {message.context.get_task().id} task should stop.")
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
                                                           status=TaskStatusValue.SUCCESS if not msg else TaskStatusValue.FAILED)
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
            # Cancel all remaining background tasks to prevent them from running indefinitely
            await self.clean_background_tasks()

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

    async def clean_background_tasks(self):
        if not self.background_tasks:
            return
        logger.info(f"Cancelling {len(self.background_tasks)} remaining background tasks for task {self.task.id}")
        for task in self.background_tasks.copy():
            if not task.done():
                task.cancel()
        # Wait for cancelled tasks to complete, but don't wait too long
        try:
            await asyncio.wait(self.background_tasks, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"Some background tasks for task {self.task.id} didn't cancel within timeout")
        except Exception as e:
            logger.warning(f"Error waiting for background tasks cancellation: {e}")
        # Clear the set as all tasks should be done now
        self.background_tasks.clear()

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
            # trajectory_strategy = self.conf.get('trajectory_strategy', None)
            # trajectory = await generate_trajectory_from_strategy(self.task.id, trajectory_strategy, self)
            # self._task_response.trajectory = self.trajectory_dataset.data
            
            traj = await self.context.get_task_trajectory(self.task.id)
            logger.debug(f"{self.task.id}|{self.task.is_sub_task}#trajectory from context: {traj}")
            logger.debug(f"{self.task.id}|{self.task.is_sub_task}#task_graph from context: {self.context._task_graph}")
            if traj:
                self._task_response.trajectory = [step.to_dict() for step in traj]
                logger.debug(f"{self.task.id}|{self.task.is_sub_task}#_task_response.trajectory: {json.dumps(self._task_response.trajectory, ensure_ascii=False)}")

            # self._task_response.trajectory = list(self.context.trajectories.values())
            # logger.warn(f"new trajectory: {json.dumps(self.trajectory_dataset.data, ensure_ascii=False)}")

        except Exception as e:
            logger.error(f"Failed to get trajectories: {str(e)}.{traceback.format_exc()}")

    async def should_stop_task(self, message: Message):
        task_flag = self.task_flag
        time_cost = time.time() - self.start_time

        # Check timeout
        if 0 < self.task.timeout < time_cost:
            logger.warn(
                f"{task_flag} task {self.task.id} timeout after {time_cost} seconds.")
            self._task_response = TaskResponse(
                answer='',
                success=False,
                context=message.context if message else self.context,
                id=self.task.id,
                time_cost=(time.time() - self.start_time),
                usage=self.context.token_usage,
                msg=f'Task timeout after {time_cost} seconds.',
                status=TaskStatusValue.TIMEOUT
            )
            await self.context.update_task_status(self.task.id, TaskStatusValue.TIMEOUT)
            return True

        # Check Task status from context
        task_status = await self.context.get_task_status()
        if task_status == TaskStatusValue.INTERRUPTED or task_status == TaskStatusValue.CANCELLED:
            logger.warn(f"{task_flag} task {self.task.id} is {task_status}.")
            self._task_response = TaskResponse(
                answer='',
                success=False,
                context=message.context if message else self.context,
                id=self.task.id,
                time_cost=time_cost,
                usage=self.context.token_usage,
                msg=f'Task is {task_status}.',
                status=task_status
            )
            return True
        return False
