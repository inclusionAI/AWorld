# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import time
import traceback

import aworld.trace as trace

from functools import partial
from typing import List, Callable, Any, Optional, Dict

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
from aworld.runners.task_status_storage import (
    TaskStatusStore,
    TaskStatusRegistry,
    TaskStatus,
    InMemoryTaskStatusStore
)
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
        self.streaming_handlers = []
        self.init_messages = []
        self.background_tasks = set()
        self.state_manager = EventRuntimeStateManager.instance()

        # Task status store for cancellation/interruption control
        if not self.task_status_store:
            self.task_status_store = kwargs.get("task_status_store") or InMemoryTaskStatusStore()
        if not self.task.task_status_store:
            self.task.task_status_store = self.task_status_store

        # Custom event handlers (hooks)
        self._cancel_handler = kwargs.get("cancel_handler", None)
        self._interrupt_handler = kwargs.get("interrupt_handler", None)

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
                if self.task.streaming_mode:
                    if self.task.streaming_queue_provider:
                        await self.task.streaming_queue_provider.put(
                            Message(payload=resp, session_id=self.context.session_id, topic=TopicType.TASK_RESPONSE))
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

        # Register task status for cancellation/interruption control
        await self.task_status_store.register(self.task.id, TaskStatus.INIT)
        logger.info(f"Registered task {self.task.id} in task status store")

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
                handler_instance = HandlerFactory(handler, runner=self)
                self.handlers.append(handler_instance)
                if handler_instance.is_stream_handler():
                    self.streaming_handlers.append(handler_instance)

        await self._register_task_status_handler()

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

        await self._streaming_task(message)

        key = message.category
        logger.warn(f"Task {self.task.id} consume message: {message}")
        if key == Constants.TOOL_CALLBACK:
            logger.warn(f"Task {self.task.id} Tool callback message {message.id}")
        transformer = self.event_mng.get_transform_handler(key)
        if transformer:
            message = await event_bus.transform(message, handler=transformer)

        results = []
        handlers = self.event_mng.get_handlers(key)
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
            else:
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

    async def _streaming_task(self, message: Message):
        async def streaming_handle(message: Message):
            for handler in self.streaming_handlers:
                async for event in handler.handle(message):
                    pass
        t = asyncio.create_task(streaming_handle(message))
        self.background_tasks.add(t)
        t.add_done_callback(partial(self._task_done_callback, message=message))
        await asyncio.sleep(0)

    async def _do_run(self):
        """Task execution process in real."""
        task_flag = self.task_flag
        start = time.time()
        msg = None
        answer = None
        message = None

        # Update task status to RUNNING
        await self.task_status_store.set_status(self.task.id, TaskStatus.RUNNING)

        try:
            while True:
                # External control - Check task status before processing each message
                task_status_info = await self.task_status_store.get(self.task.id)
                should_stop_task = await self.should_stop_task(task_status_info, message)
                if should_stop_task:
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
                # Update final task status in store
                if self._task_response:
                    final_status = self._task_response.status or TaskStatus.SUCCESS
                    reason = self._task_response.msg
                    await self.task_status_store.set_status(
                        self.task.id,
                        final_status,
                        reason=reason
                    )
                    logger.info(f"Updated final task status for {self.task.id}: {final_status}")

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

    async def should_stop_task(self, task_status_info: Dict[str, Any], message: Message):
        status = task_status_info.get('status')
        reason = task_status_info.get('reason')
        msg = reason or f"Task status is {task_status_info}"
        task_flag = self.task_flag
        time_cost = time.time() - self.start_time

        if status == TaskStatus.CANCELLED:
            logger.warning(
                f"{task_flag} task {self.task.id} was cancelled. Reason: {reason}")

            # Save checkpoint before stopping
            try:
                checkpoint = await self.context.save_checkpoint_async(
                    metadata_extra={
                        "reason": reason,
                        "status": TaskStatus.CANCELLED,
                        "time_cost": time_cost
                    }
                )
                logger.info(f"Saved context checkpoint {checkpoint.id} for cancelled task {self.task.id}")
            except Exception as e:
                logger.error(f"Failed to save checkpoint for cancelled task {self.task.id}: {e}")

            self._task_response = TaskResponse(
                answer='',
                success=False,
                context=message.context if message else self.context,
                id=self.task.id,
                time_cost=time_cost,
                usage=self.context.token_usage,
                msg=f'Task cancelled: {msg}',
                status=TaskStatus.CANCELLED
            )
            return True
        elif status == TaskStatus.INTERRUPTED:
            logger.warning(
                f"{task_flag} task {self.task.id} was interrupted. Reason: {reason}")

            # Save checkpoint before stopping
            try:
                checkpoint = await self.context.save_checkpoint_async(
                    metadata_extra={
                        "reason": reason,
                        "status": TaskStatus.INTERRUPTED,
                        "time_cost": time_cost
                    }
                )
                logger.info(f"Saved context checkpoint {checkpoint.id} for interrupted task {self.task.id}")
            except Exception as e:
                logger.error(f"Failed to save checkpoint for interrupted task {self.task.id}: {e}")

            self._task_response = TaskResponse(
                answer='',
                success=False,
                context=message.context if message else self.context,
                id=self.task.id,
                time_cost=time_cost,
                usage=self.context.token_usage,
                msg=f'Task interrupted: {msg}',
                status=TaskStatus.INTERRUPTED
            )
            return True

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
                status=TaskStatus.CANCELLED
            )
            await self.task_status_store.cancel(self.task.id, reason="Task timeout")
            return True
        return False

    # process event-based cancellation/interruption
    async def _register_task_status_handler(self):
        # Register CANCEL and INTERRUPT event handlers
        # Use custom handlers if provided, otherwise use default implementations
        cancel_handler = self._cancel_handler if self._cancel_handler else self._default_cancel_handler
        interrupt_handler = self._interrupt_handler if self._interrupt_handler else self._default_interrupt_handler

        await self.event_mng.register(Constants.TASK, TopicType.CANCEL, cancel_handler)
        await self.event_mng.register(Constants.TASK, TopicType.INTERRUPT, interrupt_handler)

        handler_type = "custom" if self._cancel_handler else "default"
        logger.info(f"Registered {handler_type} cancel handler for task {self.task.id}")
        handler_type = "custom" if self._interrupt_handler else "default"
        logger.info(f"Registered {handler_type} interrupt handler for task {self.task.id}")

    async def _default_cancel_handler(self, message: Message):
        """Default handler for CANCEL event sent through event bus.

        This is the default implementation of cancel event handling. Users can provide
        their own implementation by passing a custom cancel_handler to the constructor.

        Args:
            message: Message with category=Constants.Task and topic=TopicType.CANCEL

        Returns:
            Message: Response message indicating task cancellation

        Note:
            Custom handlers should follow the same signature and return a Message.
        """
        reason = None
        if isinstance(message.payload, (TaskItem, dict)):
            reason = message.payload.get('msg') if isinstance(message.payload, dict) else message.payload.msg
        elif isinstance(message.payload, str):
            reason = message.payload

        reason = reason or "Task cancelled via event"

        logger.warning(f"Received CANCEL event for task {self.task.id}. Reason: {reason}")

        # Save current context checkpoint before cancellation
        try:
            checkpoint = await self.context.save_checkpoint_async(
                metadata_extra={
                    "reason": reason,
                    "status": TaskStatus.CANCELLED,
                    "event": "cancel"
                }
            )
            logger.info(f"Saved context checkpoint {checkpoint.id} for cancelled task {self.task.id}")
        except Exception as e:
            logger.error(f"Failed to save checkpoint for task {self.task.id}: {e}")

        # Update task status in store
        await self.task_status_store.cancel(self.task.id, reason=reason)

        # Stop the runner
        await self.stop()

        # Return a response message
        return Message(
            category=Constants.TASK,
            topic=TopicType.FINISHED,
            payload=TaskItem(msg=f"Task cancelled: {reason}", data=message.payload),
            sender=self.name,
            session_id=self.context.session_id,
            headers={"context": self.context}
        )

    async def _default_interrupt_handler(self, message: Message):
        """Default handler for INTERRUPT event sent through event bus.

        This is the default implementation of interrupt event handling. Users can provide
        their own implementation by passing a custom interrupt_handler to the constructor.

        Args:
            message: Message with topic=TopicType.INTERRUPT

        Returns:
            Message: Response message indicating task interruption

        Note:
            Custom handlers should follow the same signature and return a Message.
        """
        reason = None
        if isinstance(message.payload, (TaskItem, dict)):
            reason = message.payload.get('msg') if isinstance(message.payload, dict) else message.payload.msg
        elif isinstance(message.payload, str):
            reason = message.payload

        reason = reason or "Task interrupted via event"

        logger.warning(f"Received INTERRUPT event for task {self.task.id}. Reason: {reason}")

        # Save current context checkpoint before interruption
        try:
            checkpoint = await self.context.save_checkpoint_async(
                metadata_extra={
                    "reason": reason,
                    "status": TaskStatus.INTERRUPTED,
                    "event": "interrupt"
                }
            )
            logger.info(f"Saved context checkpoint {checkpoint.id} for interrupted task {self.task.id}")
        except Exception as e:
            logger.error(f"Failed to save checkpoint for task {self.task.id}: {e}")

        # Update task status in store
        await self.task_status_store.interrupt(self.task.id, reason=reason)

        # Stop the runner
        await self.stop()

        # Return a response message
        return Message(
            category=Constants.TASK,
            topic=TopicType.FINISHED,
            payload=TaskItem(msg=f"Task interrupted: {reason}", data=message.payload),
            sender=self.name,
            session_id=self.context.session_id,
            headers={"context": self.context}
        )

    # user can implement their own cancel/interrupt interfaces
    async def cancel_task(self, reason: Optional[str] = None):
        """Cancel the task externally.

        This allows external code to cancel a running task by updating its status
        in the task status store. The runner will detect this change in its main loop.

        Args:
            reason: Optional reason for cancellation

        Example:
            # From external code or API:
            await runner.cancel_task(reason="User requested cancellation")
        """
        if self.task_status_store.is_finished(self.task.id):
            logger.info(f"Task {self.task.id} is already finished. Cancellation ignored.")
            return
        reason = reason or "Task cancelled externally"
        logger.info(f"Cancelling task {self.task.id} externally. Reason: {reason}")
        await self.task_status_store.cancel(self.task.id, reason=reason)

    async def interrupt_task(self, reason: Optional[str] = None):
        """Interrupt the task externally.

        This allows external code to interrupt a running task by updating its status
        in the task status store. The runner will detect this change in its main loop.

        Args:
            reason: Optional reason for interruption

        Example:
            # From external code or API:
            await runner.interrupt_task(reason="System maintenance required")
        """
        if self.task_status_store.is_finished(self.task.id):
            logger.info(f"Task {self.task.id} is already finished. Cancellation ignored.")
            return
        reason = reason or "Task interrupted externally"
        logger.info(f"Interrupting task {self.task.id} externally. Reason: {reason}")
        await self.task_status_store.interrupt(self.task.id, reason=reason)

    def get_task_status_store(self) -> TaskStatusStore:
        """Get the task status store for external access.

        This allows external code to directly access the task status store
        for querying or updating task statuses.

        Returns:
            TaskStatusStore instance used by this runner

        Example:
            # From external code:
            store = runner.get_task_status_store()
            await store.cancel(task_id, reason="External cancellation")
        """
        return self.task_status_store
