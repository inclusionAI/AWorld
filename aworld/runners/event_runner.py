# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import copy
import inspect
import json
import time
import traceback
from datetime import datetime, timezone
from functools import partial
from typing import List, Callable, Any, AsyncGenerator

import aworld.trace as trace
from aworld.core.agent.base import BaseAgent, is_agent_by_name, AgentFactory
from aworld.core.common import TaskItem, ActionModel, Observation
from aworld.core.context.amni import AmniContext, ApplicationContext
from aworld.core.context.base import Context
from aworld.dataset.trajectory_storage import get_storage_instance
from aworld.core.event.base import Message, Constants, TopicType, ToolMessage, AgentMessage
from aworld.core.exceptions import AWorldRuntimeException
from aworld.core.task import Task, TaskResponse, TaskStatusValue
from aworld.core.trajectory import (
    TrajectoryBuildResult,
    TrajectoryBuildStatus,
    TrajectoryDeliveryReceipt,
    TrajectoryDeliveryState,
    TrajectoryDeliveryTargetReceipt,
    TrajectoryFidelity,
    TrajectoryReasonCode,
    TrajectorySourceKind,
    compute_trajectory_checksum,
)
from aworld.dataset.trajectory_dataset import TrajectoryDataset
from aworld.dataset.trajectory_io import (
    TrajectoryEnvelope,
    TrajectoryJsonlSink,
    TrajectorySinkConfig,
)
from aworld.core.trajectory_update_registry import (
    TrajectoryDrainResult,
    TrajectoryRegistrySealedError,
    TrajectoryRegistryState,
)
from aworld.events.manager import EventManager
from aworld.logs.util import logger, trajectory_logger
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.post_tool_progress import WATCHDOG_STATE_KEY, increment_watchdog_metric
from aworld.runners.state_manager import EventRuntimeStateManager
from aworld.runners.task_runner import TaskRunner
from aworld.trace.base import get_trace_id
from aworld.trace.instrumentation import semconv
from aworld.models.usage import normalize_usage, summarize_prompt_cache_usage
from aworld.utils.common import override_in_subclass, new_instance
from aworld.utils.serialized_util import to_serializable


class TaskEventRunner(TaskRunner):
    """Event driven task runner."""

    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self._task_response = None
        self.hooks = {}
        self.handlers = []
        self.init_messages = []
        self.background_tasks = set()
        self.state_manager = EventRuntimeStateManager.instance()
        self.inited = False
        self._trajectory_finalize_lock = asyncio.Lock()
        self._trajectory_finalize_result = None
        self._trajectory_finalize_delivery_task = None
        self._execution_started = False
        self._deferred_task_response = None
        self._task_response_publish_lock = asyncio.Lock()
        self._task_response_published = False
        self._task_response_publish_attempted = False
        self._bootstrap_complete = asyncio.Event()
        self._stream_terminal_fallback_ready = asyncio.Event()
        self._stream_terminal_fallback = None

    def _ensure_terminal_delivery_state(self) -> None:
        if not hasattr(self, "_task_response_publish_lock"):
            self._task_response_publish_lock = asyncio.Lock()
        if not hasattr(self, "_task_response_publish_attempted"):
            self._task_response_publish_attempted = False
        if not hasattr(self, "_task_response_published"):
            self._task_response_published = False
        if not hasattr(self, "_deferred_task_response"):
            self._deferred_task_response = None
        if not hasattr(self, "_bootstrap_complete"):
            self._bootstrap_complete = asyncio.Event()
        if not hasattr(self, "_stream_terminal_fallback_ready"):
            self._stream_terminal_fallback_ready = asyncio.Event()
        if not hasattr(self, "_stream_terminal_fallback"):
            self._stream_terminal_fallback = None

    def _install_stream_terminal_fallback(self, event: Message) -> None:
        self._ensure_terminal_delivery_state()
        if self._stream_terminal_fallback is None:
            self._stream_terminal_fallback = event
            self._stream_terminal_fallback_ready.set()

    def _trajectory_task_epoch(self) -> int | None:
        epoch = getattr(self.task, "trajectory_task_epoch", None)
        if epoch is None:
            epoch = self.task.conf.get("trajectory_task_epoch") if self.task.conf else None
        if epoch is not None and (
            isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0
        ):
            raise ValueError("trajectory_task_epoch must be a non-negative integer")
        return epoch

    async def run(self) -> Any:
        """Preserve the primary failure while typing pre-execution outcomes."""
        self._ensure_terminal_delivery_state()
        primary_error: BaseException | None = None
        try:
            await self.pre_run()
            self._bootstrap_complete.set()
            await self._daemon_run()
            return await self.do_run()
        except BaseException as exc:
            primary_error = exc
            self._exception = exc
            if not self._execution_started:
                _, _, finalize_exc = await self._join_terminal_finalization(
                    self._finalize_execution_not_started_for_delivery
                )
                if finalize_exc is not None:
                    logger.warning(
                        "Failed to finalize execution-not-started trajectory: {}", finalize_exc
                    )
            raise
        finally:
            self._bootstrap_complete.set()
            try:
                await self.post_run()
            except BaseException as post_exc:
                if primary_error is None:
                    raise
                logger.warning("post_run failed after primary task failure: {}", post_exc)

    @staticmethod
    def _normalize_token_usage(token_usage: dict | None) -> dict:
        return normalize_usage(token_usage)

    @staticmethod
    def _format_task_finished_message(
        *,
        task_id: str,
        is_sub_task: bool,
        time_cost: float,
        token_usage: dict | None,
    ) -> str:
        task_scope = "sub" if is_sub_task else "main"
        normalized_usage = TaskEventRunner._normalize_token_usage(token_usage)
        message = (
            f"{task_scope} task {task_id} finished, time cost: {time_cost}s, "
            f"token cost: {normalized_usage}."
        )
        cache_summary = summarize_prompt_cache_usage(token_usage)
        if cache_summary:
            message += f" prompt cache: {cache_summary}."
        return message

    def _current_token_usage(self) -> dict:
        return self._normalize_token_usage(self.context.token_usage if self.context else {})

    def _post_tool_watchdog_timeout_seconds(self) -> float:
        return float(self.task.conf.get("post_tool_progress_watchdog_timeout_seconds", 15) or 15)

    def _post_tool_watchdog_poll_seconds(self) -> float:
        timeout_seconds = self._post_tool_watchdog_timeout_seconds()
        poll_seconds = float(self.task.conf.get("post_tool_progress_watchdog_poll_seconds", 1) or 1)
        return min(max(poll_seconds, 0.1), max(timeout_seconds, 0.1))

    async def _check_post_tool_progress_watchdog(self) -> bool:
        state = self.context.context_info.get(WATCHDOG_STATE_KEY)
        if not isinstance(state, dict):
            return False

        armed_at = float(state.get("armed_at") or 0.0)
        timeout_seconds = self._post_tool_watchdog_timeout_seconds()
        if armed_at <= 0 or (time.time() - armed_at) < timeout_seconds:
            return False

        increment_watchdog_metric(self.context, "watchdog_trigger_count")

        retry_count = int(state.get("retry_count", 0) or 0)
        if retry_count == 0:
            observation_payload = state.get("followup_observation") or {}
            observation = Observation(**observation_payload)
            retry_context = self.context.deep_copy()
            retry_context._task = self.context.get_task()
            retry_message = AgentMessage(
                payload=observation,
                sender=state.get("followup_sender") or state.get("tool_name") or "tool",
                receiver=state.get("agent_id"),
                session_id=self.context.session_id,
                headers={
                    "context": retry_context,
                    "history_sanitized_retry": True,
                    "post_tool_watchdog_retry": True,
                },
            )
            next_state = dict(state)
            next_state["retry_count"] = 1
            next_state["armed_at"] = time.time()
            next_state["retry_message_id"] = retry_message.id
            self.context.context_info[WATCHDOG_STATE_KEY] = next_state
            increment_watchdog_metric(self.context, "sanitized_history_retry_count")
            await self.event_mng.emit_message(retry_message)
            logger.warning(
                "post-tool progress watchdog retried agent %s after %.2fs without a new LLM round",
                state.get("agent_id"),
                timeout_seconds,
            )
            return True

        reason = (
            "post-tool progress watchdog: tool succeeded but the agent neither started the next LLM round "
            f"nor finished after retry. agent={state.get('agent_id')}, tool={state.get('tool_name')}, "
            f"tool_call_ids={state.get('tool_call_ids')}"
        )
        self.context.context_info.pop(WATCHDOG_STATE_KEY, None)
        await self.event_mng.emit_message(
            Message(
                category=Constants.TASK,
                payload=TaskItem(msg=reason, data=state, stop=True),
                sender=self.__class__.__name__,
                session_id=self.context.session_id,
                topic=TopicType.ERROR,
                headers={"context": self.context},
            )
        )
        logger.error(reason)
        return True


    async def do_run(self, context: Context = None):
        if self.swarm and not self.swarm.initialized:
            raise AWorldRuntimeException("swarm needs to use `reset` to init first.")
        if not self.init_messages:
            raise AWorldRuntimeException("no question event to solve.")

        async with trace.task_span(self.init_messages[0].session_id,
                                   task=self.task,
                                   attributes={semconv.TRACE_ID: self.context.trace_id}):
            resp = None
            primary_error: BaseException | None = None
            try:
                for msg in self.init_messages:
                    await self.event_mng.emit_message(msg)
                self._execution_started = True
                await self._do_run()
                await self._finalize_for_delivery()
                resp = self._response()
                time_cost = time.time() - self.start_time
                token_usage = self._current_token_usage()
                logger.info(
                    self._format_task_finished_message(
                        task_id=self.task.id,
                        is_sub_task=self.task.is_sub_task,
                        time_cost=time_cost,
                        token_usage=token_usage,
                    )
                )

                # Hooks V2: 触发 TASK_COMPLETED hook（所有任务，包括子任务）
                try:
                    from aworld.runners.hook.hooks import HookPoint
                    from aworld.runners.hook.utils import run_hooks

                    task_completed_payload = {
                        'event': 'task_completed',
                        'task_id': self.task.id,
                        'task_name': self.task.name,
                        'session_id': self.context.session_id,
                        'is_sub_task': self.task.is_sub_task,
                        'time_cost': time_cost,
                        'token_usage': token_usage,
                        'status': 'success',
                        'timestamp': time.time()
                    }

                    async for _ in run_hooks(
                        context=self.context,
                        hook_point=HookPoint.TASK_COMPLETED,
                        hook_from='task_runner',
                        payload=task_completed_payload,
                        workspace_path=getattr(self.context, 'workspace_path', None)
                    ):
                        pass
                except Exception as e:
                    logger.warning(f"TASK_COMPLETED hook execution failed for task {self.task.id}: {e}")

                # Hooks V2: 触发 session_finished hook（仅主任务）
                if not self.task.is_sub_task:
                    try:
                        from aworld.runners.hook.hooks import HookPoint
                        from aworld.runners.hook.utils import run_hooks

                        session_finished_msg = Message(
                            category='session_lifecycle',
                            payload={
                                'event': 'session_finished',
                                'session_id': self.context.session_id,
                                'task_id': self.task.id,
                                'time_cost': time_cost,
                                'token_usage': token_usage,
                                'status': 'success'
                            },
                            session_id=self.context.session_id,
                            sender='task_runner'
                        )
                        session_finished_msg.context = self.context

                        async for _ in run_hooks(
                            context=self.context,
                            hook_point=HookPoint.SESSION_FINISHED,
                            hook_from='task_runner',
                            message=session_finished_msg,
                            workspace_path=getattr(self.context, 'workspace_path', None)
                        ):
                            pass
                    except Exception as e:
                        logger.warning(f"SESSION_FINISHED hook execution failed: {e}")

                return resp
            except Exception as e:
                primary_error = e
                # Hooks V2: 触发 session_failed hook（仅主任务）
                if not self.task.is_sub_task:
                    try:
                        from aworld.runners.hook.hooks import HookPoint
                        from aworld.runners.hook.utils import run_hooks

                        session_failed_msg = Message(
                            category='session_lifecycle',
                            payload={
                                'event': 'session_failed',
                                'session_id': self.context.session_id,
                                'task_id': self.task.id,
                                'time_cost': time.time() - self.start_time,
                                'error': str(e),
                                'error_type': type(e).__name__,
                                'status': 'failed'
                            },
                            session_id=self.context.session_id,
                            sender='task_runner'
                        )
                        session_failed_msg.context = self.context

                        async for _ in run_hooks(
                            context=self.context,
                            hook_point=HookPoint.SESSION_FAILED,
                            hook_from='task_runner',
                            message=session_failed_msg,
                            workspace_path=getattr(self.context, 'workspace_path', None)
                        ):
                            pass
                    except Exception as hook_e:
                        logger.warning(f"SESSION_FAILED hook execution failed: {hook_e}")

                # 重新抛出原始异常
                raise
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                # Finalization is idempotent and also runs for exception/cancel paths.
                # It must complete before TaskResponse delivery and dataset release.
                _, deferred_cancel, finalize_exc = (
                    await self._join_terminal_finalization(
                        self._finalize_for_delivery
                    )
                )
                cleanup_error: BaseException | None = None
                if finalize_exc is not None:
                    if not isinstance(finalize_exc, asyncio.CancelledError):
                        cleanup_error = finalize_exc
                    logger.warning(
                        "Trajectory finalize failed during terminal cleanup: {}",
                        finalize_exc,
                    )

                try:
                    await self._publish_task_response_once()
                except asyncio.CancelledError as exc:
                    deferred_cancel = deferred_cancel or exc
                except BaseException as publish_exc:
                    cleanup_error = cleanup_error or publish_exc
                    logger.warning("TaskResponse publication failed during terminal cleanup: {}", publish_exc)

                # the last step mark output finished
                if not self.task.is_sub_task:
                    logger.info(f'main task {self.task.id} will mark outputs finished')
                    try:
                        await self.task.outputs.mark_completed(
                            resp if resp is not None else self._response()
                        )
                    except asyncio.CancelledError as exc:
                        deferred_cancel = deferred_cancel or exc
                    except BaseException as outputs_exc:
                        cleanup_error = cleanup_error or outputs_exc
                        logger.warning("Output completion failed during terminal cleanup: {}", outputs_exc)
                    # Snapshot to avoid iteration issues if AgentFactory registry changes during awaits.
                    agents_snapshot = list(AgentFactory._agent_instance.values())
                    for agent in agents_snapshot:
                        if agent and agent.sandbox:
                            sandbox = agent.sandbox
                            # task_list tracks which root-tasks are using this sandbox.
                            # Only cleanup when the current task id is no longer referenced.
                            try:
                                metadata = getattr(sandbox, "metadata", {})
                                task_list = metadata.get("task_list", [])
                                
                                # If we can't find/understand task_list, fallback to original behavior.
                                if not task_list:
                                    await sandbox.cleanup()
                                    continue

                                # Remove current task id from task_list (dedup by deletion).
                                task_id = self.task.id
                                if task_id in task_list:
                                    task_list.remove(task_id)
                                if len(task_list) == 0:
                                    await sandbox.cleanup()
                                
                            except asyncio.CancelledError as exc:
                                deferred_cancel = deferred_cancel or exc
                            except Exception as e:
                                logger.warning(
                                    f"Failed to manage sandbox cleanup for agent {agent.id() if hasattr(agent, 'id') else ''}: {e}"
                                )
                                # Keep the original semantics to avoid leaked resources.
                                try:
                                    await sandbox.cleanup()
                                except asyncio.CancelledError as exc:
                                    deferred_cancel = deferred_cancel or exc
                                except BaseException as cleanup_exc:
                                    cleanup_error = cleanup_error or cleanup_exc
                                    logger.warning(
                                        "Sandbox fallback cleanup failed: {}", cleanup_exc
                                    )
                    # Release trajectory storage to free memory; trajectories have already
                    # been persisted by _save_trajectories() before reaching this point.
                    self.context.trajectory_dataset = None
                if deferred_cancel is not None:
                    raise deferred_cancel
                if cleanup_error is not None and primary_error is None:
                    raise cleanup_error



    async def pre_run(self):
        logger.debug(f"task {self.task.id} pre run start...")
        self._trajectory_task_epoch()
        await super().pre_run()

        # Hooks V2: 触发 TASK_CREATED hook（所有任务，包括子任务）
        try:
            from aworld.runners.hook.hooks import HookPoint
            from aworld.runners.hook.utils import run_hooks

            task_created_payload = {
                'event': 'task_created',
                'task_id': self.task.id,
                'task_name': self.task.name,
                'session_id': self.context.session_id if hasattr(self, 'context') and self.context else None,
                'is_sub_task': self.task.is_sub_task,
                'input': str(self.task.input)[:500] if self.task.input else None,  # 限制长度
                'timestamp': time.time()
            }

            async for _ in run_hooks(
                context=self.context if hasattr(self, 'context') else None,
                hook_point=HookPoint.TASK_CREATED,
                hook_from='task_runner',
                payload=task_created_payload,
                workspace_path=getattr(self.context, 'workspace_path', None) if hasattr(self, 'context') else None
            ):
                pass
        except Exception as e:
            logger.warning(f"TASK_CREATED hook execution failed for task {self.task.id}: {e}")
        self.event_mng = EventManager(self.context, streaming_mode=self.task.streaming_mode)
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
            self.context.trajectory_dataset = traj_dataset
        registry = self.context.root.trajectory_update_registry if isinstance(
            self.context, ApplicationContext
        ) else self.context.trajectory_update_registry
        registry.open(self.task.id)
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

        # Tool callbacks are resolved through the inner handler pipeline rather than
        # event-bus topic subscriptions, so wire the callback handler explicitly.
        from aworld.runners.callback.tool import ToolCallbackHandler
        if not any(isinstance(handler, ToolCallbackHandler) for handler in self.handlers):
            self.handlers.append(ToolCallbackHandler(self))

        self.task_flag = "sub" if self.task.is_sub_task else "main"
        self.inited = True
        self._ensure_terminal_delivery_state()
        self._bootstrap_complete.set()
        logger.debug(f"{self.task_flag} task: {self.task.id} pre run finish, will start to run...")

        # Hooks V2: 触发 session_started hook
        # 仅对主任务（非子任务）触发，因为 Session 在主任务级别管理
        if not self.task.is_sub_task:
            try:
                from aworld.runners.hook.hooks import HookPoint
                from aworld.runners.hook.utils import run_hooks

                # 创建 session_started message
                session_started_msg = Message(
                    category='session_lifecycle',
                    payload={
                        'event': 'session_started',
                        'session_id': self.context.session_id,
                        'task_id': self.task.id,
                        'start_time': self.start_time
                    },
                    session_id=self.context.session_id,
                    sender='task_runner'
                )
                session_started_msg.context = self.context

                async for _ in run_hooks(
                    context=self.context,
                    hook_point=HookPoint.SESSION_STARTED,
                    hook_from='task_runner',
                    payload=session_started_msg.payload,
                    message=session_started_msg,
                    workspace_path=getattr(self.context, 'workspace_path', None)
                ):
                    pass
            except Exception as e:
                logger.warning(f"SESSION_STARTED hook execution failed: {e}")

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
        async with trace.message_span(message=message, attributes={semconv.TRACE_ID: self.context.trace_id}):
            logger.debug(f"start_message_node message id: {message.id} of task {self.task.id}")
            self.state_manager.start_message_node(message)
            self._schedule_trajectory_update(message, revision=0)
            if handlers:
                handler_list = handlers.get(message.topic) or handlers.get(message.receiver)
                if not handler_list:
                    logger.warning(f"{message.topic}/{message.receiver} no handler, ignore.")
                    handlers = []
                else:
                    handle_map = {}

                    for handler in handler_list:
                        t = asyncio.create_task(self._handle_task(message, handler))
                        self.background_tasks.add(t)
                        handle_map[t] = False
                    for t, _ in handle_map.items():
                        t.add_done_callback(partial(self._task_done_callback, group=handle_map, message=message))
                        await asyncio.sleep(0)
            else:
                if message.category in [Constants.TOOL, Constants.AGENT]:
                    logger.info(f"Task {self.task.id} with key {key} cannot get handlers, use inner_handlers. message: {message}")
                    if message.receiver and message.receiver not in inner_handlers:
                        logger.warning(
                            f"Task {self.task.id} {message.receiver} no handler, ignore."
                            f"current subscriber: {self.event_mng.event_bus._subscribers}"
                        )
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
        if not task.cancelled():
            try:
                task.exception()
            except Exception:
                pass
        if not group:
            self.state_manager.end_message_node(message)
            self._schedule_trajectory_update(message, revision=2)
        else:
            group[task] = True
            if all([v for _, v in group.items()]):
                self.state_manager.end_message_node(message)
                self._schedule_trajectory_update(message, revision=2)

    async def _handle_task(self, message: Message, handler: Callable[..., Any]):
        con = message
        async with trace.handler_span(message=message,
                                      handler=handler,
                                      attributes={semconv.TRACE_ID: self.context.trace_id}):
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
                        await self._emit_or_defer_task_response(event)
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
            await self._emit_or_defer_task_response(event)

    async def _emit_or_defer_task_response(self, event: Message):
        """Keep terminal responses private until the finalized snapshot is bound."""
        if event.topic != TopicType.TASK_RESPONSE:
            return await self.event_mng.emit_message(event)
        payload = event.payload
        if isinstance(payload, TaskResponse):
            self._task_response = payload
        self._deferred_task_response = event
        return False

    async def _publish_task_response_once(self) -> bool:
        self._ensure_terminal_delivery_state()
        async with self._task_response_publish_lock:
            if self._task_response_publish_attempted:
                return False
            response = self._task_response
            if response is None or response.trajectory_build_result is None:
                raise RuntimeError("cannot publish TaskResponse before trajectory finalization")
            event = self._deferred_task_response
            if event is None:
                event = Message(
                    payload=response,
                    category=Constants.TASK,
                    topic=TopicType.TASK_RESPONSE,
                    sender=self.__class__.__name__,
                    session_id=getattr(self.context, "session_id", "") or "",
                    headers={"context": self.context},
                )
            else:
                event.payload = response
            # Fence the attempt before calling an emitter that may publish to one
            # destination and then raise while publishing to another.
            self._task_response_publish_attempted = True
            event_manager = getattr(self, "event_mng", None)
            if event_manager is None:
                self._install_stream_terminal_fallback(event)
                return False
            try:
                await event_manager.emit_message(event)
            except asyncio.CancelledError:
                # The attempt fence has already been raised. Make the exact
                # finalized event available to a blocked local stream before
                # preserving cancellation; a retry could duplicate a partial
                # EventManager publication.
                self._install_stream_terminal_fallback(event)
                raise
            except Exception as exc:
                logger.warning("Terminal TaskResponse emit failed: {}", exc)
                self._install_stream_terminal_fallback(event)
                return False
            self._task_response_published = True
            return True

    async def _inner_handler_process(self, results: List[Message], handlers: List[DefaultHandler]):
        # can use runtime backend to parallel
        for handler in handlers:
            for result in results:
                if await self.should_stop_task(result):
                    await self.stop()
                    return
                handler_result = handler.handle(result)
                if inspect.isasyncgen(handler_result):
                    async for event in handler_result:
                        yield event
                    continue

                event = await handler_result
                if event:
                    yield event

    def _is_trajectory_source_message(self, message: Message) -> bool:
        context = getattr(message, "context", None)
        if context is None or context.task_id != self.task.id or message.category != Constants.AGENT:
            return False
        if not message.sender or not message.receiver or not is_agent_by_name(message.receiver):
            return False
        return not message.headers.get("agent_as_tool", False)

    def _trajectory_registry(self):
        if isinstance(self.context, ApplicationContext):
            return self.context.root.trajectory_update_registry
        return self.context.trajectory_update_registry

    def _schedule_trajectory_update(self, message: Message, *, revision: int):
        if not self._is_trajectory_source_message(message):
            return None
        try:
            return self._trajectory_registry().schedule(
                task_id=self.task.id,
                logical_step_id=str(message.id),
                revision=revision,
                update_factory=lambda: self._update_trajectory(message, revision=revision),
            )
        except TrajectoryRegistrySealedError as exc:
            logger.warning("Rejected late trajectory update for message {}: {}", message.id, exc)
            return None

    async def _update_trajectory(self, message: Message, *, revision: int = 1):
        return await self.context.update_task_trajectory(
            message,
            self.task.id,
            logical_step_id=str(message.id),
            revision=revision,
            _registry_managed=True,
        )

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
                    logger.warn(f"Runner {message.context.get_task().id if message else self.task.id} task should stop.")
                    await self.stop()
                else:
                    self._stopped.clear()
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
                                                           usage=self._current_token_usage(),
                                                           status=TaskStatusValue.SUCCESS if not msg else TaskStatusValue.FAILED)
                    break
                logger.debug(f"{task_flag} task {self.task.id} next message snap")
                # consume message
                try:
                    message = await asyncio.wait_for(
                        self.event_mng.consume(),
                        timeout=self._post_tool_watchdog_poll_seconds(),
                    )
                except asyncio.TimeoutError:
                    await self._check_post_tool_progress_watchdog()
                    continue
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
                                pass
                                #await agent.sandbox.cleanup()
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
            _, pending = await asyncio.wait(set(self.background_tasks), timeout=5.0)
            if pending:
                logger.warning(f"Some background tasks for task {self.task.id} didn't cancel within timeout")
                self._trajectory_registry().mark_source_not_finalized(self.task.id)
            self.background_tasks.intersection_update(pending)
        except Exception as e:
            self._trajectory_registry().mark_source_not_finalized(self.task.id)
            logger.warning(f"Error waiting for background tasks cancellation: {e}")

    async def _quiesce_trajectory_producers(self) -> None:
        """Flush handler completion callbacks before freezing the registry HWM."""
        await asyncio.sleep(0)
        background_tasks = getattr(self, "background_tasks", set())
        if any(not task.done() for task in background_tasks):
            await self.clean_background_tasks()
        # asyncio task done callbacks schedule the final message revision.
        await asyncio.sleep(0)

    async def _run_finalize_for_delivery_attempt(self) -> TrajectoryBuildResult:
        await self._quiesce_trajectory_producers()
        return await self._save_trajectories()

    async def _await_trajectory_finalize_attempt(self, finalize) -> TrajectoryBuildResult:
        if not hasattr(self, "_trajectory_finalize_delivery_task"):
            self._trajectory_finalize_delivery_task = None
        attempt = self._trajectory_finalize_delivery_task
        if attempt is None:
            attempt = asyncio.create_task(finalize())
            self._trajectory_finalize_delivery_task = attempt
        return await asyncio.shield(attempt)

    async def _join_terminal_finalization(self, finalize):
        """Defer arbitrary caller cancellation until terminal state is ready.

        Cleanup cancellation is level-triggered by callers and may arrive more
        than once. Each interruption rejoins the same shielded task-scoped
        build/export attempt. Publication is at-most-once and installs a local
        fallback before propagating cancellation, so the next state-machine
        iteration can finish without repeating an external side effect.
        """
        deferred_cancel: asyncio.CancelledError | None = None
        while True:
            try:
                result = await finalize()
                return result, deferred_cancel, None
            except asyncio.CancelledError as exc:
                deferred_cancel = deferred_cancel or exc
                attempt = getattr(self, "_trajectory_finalize_delivery_task", None)
                if attempt is not None and attempt.done() and attempt.cancelled():
                    # This is cancellation of the cached producer itself, not
                    # another interruption of its caller; retrying it can never
                    # make progress and would spin forever.
                    return None, deferred_cancel, exc
                continue
            except BaseException as exc:
                return None, deferred_cancel, exc

    async def _finalize_for_delivery(self) -> TrajectoryBuildResult:
        """Await the one task-scoped finalize/delivery attempt.

        Shielding the cached task is essential for thread-backed exporters:
        cancelling an awaiter cannot stop an append already running in a
        worker thread, so rebuilding the attempt would write the same revision
        twice with different creation metadata.
        """
        result = await self._await_trajectory_finalize_attempt(
            self._run_finalize_for_delivery_attempt
        )
        # Publication is intentionally outside the shield: cancellation must
        # reach the emitter so it can install the runner-local fallback before
        # it is re-raised. Only the non-repeatable build/export attempt needs
        # cancellation protection.
        await self._publish_task_response_once()
        return result

    async def stop(self):
        self._stopped.set()

    async def is_stopped(self):
        return self._stopped.is_set()

    def response(self):
        return self._task_response

    def _response(self):
        if self._task_response is None:
            self._task_response = TaskResponse(id=self.context.task_id if self.context else "",
                                               success=False,
                                               msg="Task return None.",
                                               status=TaskStatusValue.FAILED)
        task_conf = self.context.get_task().conf if self.context and self.context.get_task() else None
        if task_conf and task_conf.get("resp_carry_context", True) is False:
            self._task_response.context = None
        if task_conf and task_conf.get("resp_carry_raw_llm_resp", False) is True:
            self._task_response.raw_llm_resp = self.context.context_info.get('llm_output')
        self._task_response.llm_calls = copy.deepcopy(self.context.context_info.get("llm_calls", []))
        self._task_response.trace_id = get_trace_id()
        return self._task_response

    @staticmethod
    def _delivery_not_requested() -> TrajectoryDeliveryTargetReceipt:
        return TrajectoryDeliveryTargetReceipt(
            status=TrajectoryDeliveryState.NOT_REQUESTED,
            reason_code="format_not_requested",
        )

    @staticmethod
    def _delivery_failed(
        error_code: str, *, record_checksum: str | None = None
    ) -> TrajectoryDeliveryTargetReceipt:
        return TrajectoryDeliveryTargetReceipt(
            status=TrajectoryDeliveryState.FAILED,
            record_checksum=record_checksum,
            error_code=error_code,
        )

    async def _deliver_trajectory(
        self,
        *,
        build_result: TrajectoryBuildResult,
        inline_trajectory: list[dict[str, Any]],
        llm_calls: list[dict[str, Any]],
        runner_conf: Any,
    ) -> TrajectoryDeliveryReceipt:
        """Deliver compatibility projections behind a fail-open observability boundary."""
        try:
            sink_config = TrajectorySinkConfig.from_sources(runner_conf)
        except Exception as exc:
            logger.warning("Failed to resolve trajectory sink config: {}", exc)
            failed = self._delivery_failed("sink_config_invalid")
            return TrajectoryDeliveryReceipt(
                requested_format="invalid", legacy=failed, v2=failed
            )

        requested_format = sink_config.format.value
        legacy = self._delivery_not_requested()
        v2 = self._delivery_not_requested()

        if sink_config.writes_legacy:
            try:
                context = getattr(self, "context", None)
                token_ids = getattr(context, "token_id_traj", None)
                token_id_traj = (
                    json.dumps(to_serializable(token_ids)) if token_ids else None
                )
                payload = {
                    "task_id": self.task.id,
                    "is_sub_task": self.task.is_sub_task,
                    "trajectory": json.dumps(
                        to_serializable(inline_trajectory), ensure_ascii=False
                    ),
                    "token_id_trajectory": token_id_traj,
                    "llm_calls": json.dumps(
                        copy.deepcopy(llm_calls), ensure_ascii=False
                    ),
                    "trajectory_build_result": build_result.to_dict(),
                }
                trajectory_logger.info(f"{payload}")
                legacy = TrajectoryDeliveryTargetReceipt(
                    # Loguru accepted the record, but its configured sinks do
                    # not expose a durable append acknowledgement here.
                    status=TrajectoryDeliveryState.EMITTED,
                    reason_code="legacy_sink_unacknowledged",
                )
            except Exception as exc:
                logger.warning("Failed to emit finalized legacy trajectory: {}", exc)
                legacy = self._delivery_failed("legacy_emit_failed")

        if sink_config.writes_v2:
            record_checksum = None
            try:
                context = getattr(self, "context", None)
                token_ids = getattr(context, "token_id_traj", None)
                token_id_trajectory = (
                    to_serializable(token_ids) if token_ids else None
                )
                epoch = self._trajectory_task_epoch()
                envelope = TrajectoryEnvelope(
                    build_result=build_result,
                    revision=(epoch + 1) if epoch is not None else 1,
                    trajectory=(
                        None if build_result.trajectory_ref is not None else inline_trajectory
                    ),
                    llm_calls=copy.deepcopy(llm_calls),
                    token_id_trajectory=token_id_trajectory,
                    is_sub_task=self.task.is_sub_task,
                )
                record_checksum = envelope.to_dict()["integrity"]["record_checksum"]
            except Exception as exc:
                logger.warning("Failed to construct trajectory JSONL v2 envelope: {}", exc)
                v2 = self._delivery_failed("v2_envelope_failed")
            else:
                try:
                    acknowledgement = await asyncio.to_thread(
                        TrajectoryJsonlSink(sink_config).append, envelope
                    )
                    if acknowledgement is None:
                        v2 = self._delivery_failed("v2_append_not_acknowledged")
                    else:
                        v2 = TrajectoryDeliveryTargetReceipt(
                            status=TrajectoryDeliveryState.PERSISTED,
                            record_checksum=record_checksum,
                        )
                except Exception as exc:
                    logger.warning("Failed to emit trajectory JSONL v2 snapshot: {}", exc)
                    v2 = self._delivery_failed(
                        "v2_append_failed", record_checksum=record_checksum
                    )

        return TrajectoryDeliveryReceipt(
            requested_format=requested_format,
            legacy=legacy,
            v2=v2,
        )

    async def _safe_deliver_trajectory(self, **kwargs) -> TrajectoryDeliveryReceipt:
        """Ultimate exporter guard: observability must never change task outcome."""
        try:
            return await self._deliver_trajectory(**kwargs)
        except Exception as exc:
            logger.warning("Unexpected trajectory delivery failure: {}", exc)
            failed = self._delivery_failed("delivery_unexpected_failure")
            return TrajectoryDeliveryReceipt(
                requested_format="invalid", legacy=failed, v2=failed
            )

    async def _finalize_execution_not_started_for_delivery(
        self,
    ) -> TrajectoryBuildResult:
        result = await self._await_trajectory_finalize_attempt(
            self._finalize_execution_not_started
        )
        await self._publish_task_response_once()
        return result

    async def _finalize_execution_not_started(self) -> TrajectoryBuildResult:
        if not hasattr(self, "_trajectory_finalize_lock"):
            self._trajectory_finalize_lock = asyncio.Lock()
            self._trajectory_finalize_result = None
        async with self._trajectory_finalize_lock:
            if self._trajectory_finalize_result is not None:
                return self._trajectory_finalize_result
            try:
                task_epoch = self._trajectory_task_epoch()
            except ValueError:
                task_epoch = None
            context = getattr(self, "context", None) or getattr(self.task, "context", None)
            registry = None
            try:
                registry = self._trajectory_registry()
                state = registry.state(self.task.id)
                if state in {TrajectoryRegistryState.OPEN, TrajectoryRegistryState.SEALED}:
                    registry.seal(self.task.id)
                    await registry.drain(self.task.id, timeout=0)
                if registry.state(self.task.id) is TrajectoryRegistryState.DRAINED:
                    dataset_owner = (
                        context.root if isinstance(context, ApplicationContext) else context
                    )
                    dataset = getattr(dataset_owner, "trajectory_dataset", None)
                    if dataset is not None:
                        dataset.fence_task_updates(self.task.id)
                    registry.release(self.task.id)
            except Exception as exc:
                logger.warning("Failed to close pre-execution trajectory registry: {}", exc)
            build_result = TrajectoryBuildResult(
                task_id=self.task.id,
                session_id=getattr(context, "session_id", None),
                trace_id=getattr(context, "trace_id", None),
                task_epoch=task_epoch,
                status=TrajectoryBuildStatus.EMPTY,
                fidelity=TrajectoryFidelity.UNAVAILABLE,
                reason_code=TrajectoryReasonCode.EXECUTION_NOT_STARTED,
                source_kind=TrajectorySourceKind.EVENT_STATE,
                source_high_watermark=None,
                scheduled_updates=0,
                completed_updates=0,
                failed_updates=0,
                pending_updates=0,
                source_agent_messages=0,
                llm_call_count=0,
                tool_call_count=0,
                persisted_items=0,
                trajectory_ref=None,
                source_checksum=None,
                trajectory_checksum=None,
                builder_version="sar-finalize-v1",
                created_at=datetime.now(timezone.utc),
            )
            if self._task_response is None:
                self._task_response = TaskResponse(
                    id=self.task.id,
                    context=context,
                    success=False,
                    status=TaskStatusValue.FAILED,
                    msg="Task execution did not start.",
                )
            self._task_response.trajectory = []
            self._task_response.trajectory_build_result = build_result
            runner_conf = getattr(self, "conf", None) or self.task.conf or {}
            llm_calls = []
            receipt = await self._safe_deliver_trajectory(
                build_result=build_result,
                inline_trajectory=[],
                llm_calls=llm_calls,
                runner_conf=runner_conf,
            )
            self._task_response.trajectory_delivery_receipt = receipt
            self._trajectory_finalize_result = build_result
            return build_result

    async def _save_trajectories(self):
        if not hasattr(self, "_trajectory_finalize_lock"):
            self._trajectory_finalize_lock = asyncio.Lock()
            self._trajectory_finalize_result = None
        async with self._trajectory_finalize_lock:
            if self._trajectory_finalize_result is not None:
                return self._trajectory_finalize_result

            registry = self._trajectory_registry()
            if registry.state(self.task.id) is None:
                registry.open(self.task.id)
            registry.seal(self.task.id)
            runner_conf = getattr(self, "conf", None) or self.task.conf
            timeout = float(runner_conf.get("trajectory_finalize_timeout_seconds", 10) or 10)
            drain = await registry.drain(self.task.id, timeout=timeout)

            dataset_owner = self.context.root if isinstance(self.context, ApplicationContext) else self.context
            if dataset_owner.trajectory_dataset is not None:
                dataset_owner.trajectory_dataset.fence_task_updates(self.task.id)

            trajectory = []
            snapshot_error = None
            try:
                trajectory = await self.context.get_task_trajectory(self.task.id, strict=True) or []
            except Exception as exc:
                snapshot_error = exc

            inline_trajectory = []
            trajectory_checksum = None
            projection_error = None
            tool_call_count = 0
            try:
                inline_trajectory = [
                    step.to_dict() if hasattr(step, "to_dict") else to_serializable(step)
                    for step in trajectory
                ]
                trajectory_checksum = (
                    compute_trajectory_checksum(inline_trajectory)
                    if inline_trajectory
                    else None
                )
                for step in inline_trajectory:
                    action = step.get("action", {}) if isinstance(step, dict) else {}
                    calls = action.get("tool_calls", []) if isinstance(action, dict) else []
                    tool_call_count += len(calls) if isinstance(calls, list) else 0
            except Exception as exc:
                # A raw storage snapshot is not safely deliverable until its
                # SAR projection and canonical integrity checksum both finish.
                # Keep business completion independent while making the
                # observability failure explicit and non-partial.
                projection_error = exc
                inline_trajectory = []
                trajectory_checksum = None
                tool_call_count = 0
                logger.warning("Failed to project finalized trajectory snapshot: {}", exc)
            late_registrations, source_not_finalized = registry.diagnostics(self.task.id)
            all_scheduled_updates_acknowledged = (
                drain.scheduled > 0 and drain.completed == drain.scheduled
            )

            reason_code = None
            if snapshot_error is not None or projection_error is not None or drain.failed:
                reason_code = TrajectoryReasonCode.TRAJECTORY_BUILD_FAILED
            elif drain.timed_out:
                reason_code = TrajectoryReasonCode.TRAJECTORY_UPDATE_TIMEOUT
            elif source_not_finalized or late_registrations:
                reason_code = TrajectoryReasonCode.SOURCE_NOT_FINALIZED
            elif inline_trajectory and not all_scheduled_updates_acknowledged:
                reason_code = TrajectoryReasonCode.SOURCE_NOT_FINALIZED
            elif not inline_trajectory:
                reason_code = TrajectoryReasonCode.TRAJECTORY_STORAGE_EMPTY

            if projection_error is not None:
                status = TrajectoryBuildStatus.FAILED
                fidelity = TrajectoryFidelity.BUILD_FAILED
            elif inline_trajectory and reason_code is None:
                status = TrajectoryBuildStatus.COMPLETE
                fidelity = TrajectoryFidelity.COMPLETE
            elif inline_trajectory:
                status = TrajectoryBuildStatus.PARTIAL
                fidelity = TrajectoryFidelity.PARTIAL
            elif drain.timed_out or source_not_finalized or late_registrations:
                status = TrajectoryBuildStatus.PARTIAL
                fidelity = TrajectoryFidelity.PARTIAL
            elif drain.failed or snapshot_error is not None or projection_error is not None:
                status = TrajectoryBuildStatus.FAILED
                fidelity = TrajectoryFidelity.BUILD_FAILED
            else:
                status = TrajectoryBuildStatus.EMPTY
                fidelity = TrajectoryFidelity.UNAVAILABLE

            llm_calls = self.context.context_info.get("llm_calls", [])
            build_result = TrajectoryBuildResult(
                task_id=self.task.id,
                session_id=self.context.session_id,
                trace_id=self.context.trace_id,
                task_epoch=self._trajectory_task_epoch(),
                status=status,
                fidelity=fidelity,
                reason_code=reason_code,
                source_kind=TrajectorySourceKind.EVENT_STATE,
                source_high_watermark=drain.high_watermark,
                scheduled_updates=drain.scheduled,
                completed_updates=drain.completed,
                failed_updates=drain.failed,
                pending_updates=drain.pending,
                source_agent_messages=len(drain.logical_step_ids),
                llm_call_count=len(llm_calls) if isinstance(llm_calls, list) else 0,
                tool_call_count=tool_call_count,
                persisted_items=len(inline_trajectory),
                trajectory_ref=None,
                source_checksum=None,
                trajectory_checksum=trajectory_checksum,
                builder_version="sar-finalize-v1",
                created_at=datetime.now(timezone.utc),
            )

            response = self._response()
            response.trajectory = inline_trajectory
            response.trajectory_build_result = build_result

            logger.debug(f"{self.task.id}|{self.task.is_sub_task}#trajectory from context: {trajectory}")
            logger.debug(f"{self.task.id}|{self.task.is_sub_task}#task_graph from context: {self.context._task_graph}")
            receipt = await self._safe_deliver_trajectory(
                build_result=build_result,
                inline_trajectory=inline_trajectory,
                llm_calls=copy.deepcopy(llm_calls) if isinstance(llm_calls, list) else [],
                runner_conf=runner_conf,
            )
            response.trajectory_delivery_receipt = receipt

            self._trajectory_finalize_result = build_result
            registry.release(self.task.id)
            return build_result

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
                usage=self._current_token_usage(),
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
                usage=self._current_token_usage(),
                msg=f'Task is {task_status}.',
                status=task_status
            )
            return True

        # Check if all background tasks are done
        if isinstance(self.context, ApplicationContext):
            need_pending = self.context.has_pending_background_tasks(
                agent_id=self.context.agent_info.current_agent_id if self.context.agent_info and hasattr(self.context.agent_info, 'current_agent_id') else "",
                parent_task_id=self.context.task_id)
            if need_pending:
                return False

        return await self.is_stopped()

    async def streaming(self) -> AsyncGenerator[Message, None]:
        if not self.task.streaming_mode:
            logger.warning(f"Task {self.task.id} is not in streaming mode")
            return

        self._ensure_terminal_delivery_state()
        if not getattr(self, "inited", False):
            bootstrap_wait = asyncio.create_task(self._bootstrap_complete.wait())
            fallback_wait = asyncio.create_task(
                self._stream_terminal_fallback_ready.wait()
            )
            try:
                await asyncio.wait(
                    {bootstrap_wait, fallback_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in (bootstrap_wait, fallback_wait):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    bootstrap_wait, fallback_wait, return_exceptions=True
                )

        event_manager = getattr(self, "event_mng", None)
        streaming_eventbus = (
            getattr(event_manager, "streaming_eventbus", None)
            if event_manager is not None
            else None
        )
        if not streaming_eventbus:
            if self._stream_terminal_fallback is not None:
                yield self._stream_terminal_fallback
                return
            logger.warning(f"Task {self.task.id} has no streaming_eventbus configured")
            return

        def is_task_end_msg(msg: Message):
            return msg and isinstance(msg, Message) and msg.topic == TopicType.TASK_RESPONSE

        try:
            while True:
                bus_get = asyncio.create_task(streaming_eventbus.get(self.task.id))
                fallback_wait = asyncio.create_task(
                    self._stream_terminal_fallback_ready.wait()
                )
                try:
                    done, _ = await asyncio.wait(
                        {bus_get, fallback_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if fallback_wait in done and bus_get not in done:
                        # A partially successful emitter can enqueue the bus
                        # terminal immediately before installing the fallback.
                        # Give that already-ready bus delivery one scheduling turn.
                        await asyncio.sleep(0)
                    if bus_get.done():
                        try:
                            msg = bus_get.result()
                        except Exception:
                            if self._stream_terminal_fallback is None:
                                raise
                            msg = self._stream_terminal_fallback
                    else:
                        msg = self._stream_terminal_fallback
                finally:
                    for task in (bus_get, fallback_wait):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(bus_get, fallback_wait, return_exceptions=True)
                if msg is None:
                    continue
                yield msg
                # End the loop when receiving end signal
                if is_task_end_msg(msg):
                    break
        except asyncio.TimeoutError:
            logger.warning(f"Streaming queue timeout for task {self.task.id}")
        except Exception as e:
            logger.error(f"Error reading from streaming queue: {e}")
            raise
