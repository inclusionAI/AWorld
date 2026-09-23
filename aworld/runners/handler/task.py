# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import json
import time
import traceback
from typing import AsyncGenerator, TYPE_CHECKING

from aworld.core.common import TaskItem
from aworld.core.event.base import Message, Constants, TopicType
from aworld.core.task import TaskFailureOrigin, TaskResponse, TaskStatusValue
from aworld.core.tool.base import Tool, AsyncTool
from aworld.logs.util import logger, trajectory_logger
from aworld.output import Output
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import HookPoint
from aworld.utils.serialized_util import to_serializable
from aworld.core.context.compiler import CompletionMode, CompletionStatus

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
    @staticmethod
    def _build_user_safe_error_answer(raw_msg: str | None) -> str:
        if not raw_msg:
            return "Task fail, cause: internal runtime error."

        message = str(raw_msg)
        lowered = message.lower()

        if "tool_calls mismatch" in lowered:
            return "Task fail, cause: internal tool-call reconciliation error."

        if "traceback" in lowered or "messages:" in lowered or len(message) > 1000:
            return "Task fail, cause: internal runtime error."

        return f"Task fail, cause: {message}"

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
            failure = message.headers.get("task_failure")
            if not isinstance(failure, dict):
                failure = task_item.failure
            if not isinstance(failure, dict):
                failure = {}
            origin = failure.get("origin")
            if origin not in {item.value for item in TaskFailureOrigin}:
                origin = TaskFailureOrigin.INFRASTRUCTURE.value
            code = failure.get("code")
            error_type = failure.get("error_type")
            failure_code = code if isinstance(code, str) else "runtime_exception"
            task_owned = origin == TaskFailureOrigin.TASK.value
            response_status = (
                TaskStatusValue.INCOMPLETE if task_owned else TaskStatusValue.FAILED
            )
            recoverable = task_owned or failure_code in {
                "provider_timeout",
                "idle_timeout",
                "call_deadline_exceeded",
                "action_repair_timeout",
            }
            logger.warning(
                "AWORLD_TASK_RESPONSE_FAILURE="
                + json.dumps(
                    {
                        "schema_version": "aworld.task.response-failure.v1",
                        "task_id": self.runner.task.id,
                        "task_status": response_status,
                        "semantic_status": "incomplete",
                        "failure_origin": origin,
                        "failure_code": failure_code,
                        "error_type": error_type if isinstance(error_type, str) else None,
                        "recoverable": recoverable,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            self.runner._task_response = TaskResponse(msg=task_item.msg,
                                                      answer=self._build_user_safe_error_answer(task_item.msg),
                                                      context=message.context,
                                                      success=False,
                                                      id=self.runner.task.id,
                                                      time_cost=(time.time() - self.runner.start_time),
                                                      usage=self.runner.context.token_usage,
                                                      status=response_status,
                                                      failure_origin=origin,
                                                      failure_code=failure_code,
                                                      error_type=error_type if isinstance(error_type, str) else None,
                                                      semantic_status="incomplete",
                                                      completion_reason=failure_code,
                                                      recoverable=recoverable)
            await self.runner.stop()
            yield Message(payload=self.runner._task_response,
                          session_id=message.session_id,
                          headers=message.headers,
                          topic=TopicType.TASK_RESPONSE)
        elif topic == TopicType.FINISHED:
            async for event in self.run_hooks(message, HookPoint.FINISHED):
                yield event

            completion = self.runner.context.assess_completion_contract(
                agent_claimed_finished=True
            )
            completion_blocked = (
                completion is not None
                and completion.mode is CompletionMode.ENFORCE
                and completion.status is not CompletionStatus.SATISFIED
            )
            execution_state = self.runner.context.context_info.get("agent_execution_state", {})
            if not isinstance(execution_state, dict) or (
                execution_state.get("schema_version") != "aworld.agent.execution-state/v1"
                or execution_state.get("task_id") != self.runner.context.task_id
                or execution_state.get("task_epoch") != getattr(self.runner.context, "task_epoch", None)
            ):
                execution_state = {}
            semantic_status = execution_state.get("status")
            if semantic_status == "running":
                semantic_status = "incomplete"
                execution_state = {**execution_state, "reason": "completion_not_confirmed", "recoverable": True}
            incomplete = semantic_status in {"incomplete", "budget_exhausted"}
            reason = execution_state.get("reason") if incomplete else None
            if completion_blocked:
                semantic_status = "incomplete"
                reason = "completion_contract_unsatisfied"
            completion_infrastructure_failure = self.runner.context.context_info.get(
                "completion_infrastructure_failure"
            )
            if not isinstance(completion_infrastructure_failure, dict):
                completion_infrastructure_failure = {}
            completion_failure_is_infrastructure = bool(
                completion_blocked
                and completion_infrastructure_failure.get("failure_code")
            )
            unsuccessful = completion_blocked or incomplete
            status = (
                TaskStatusValue.BUDGET_EXHAUSTED if semantic_status == "budget_exhausted"
                else TaskStatusValue.INCOMPLETE if unsuccessful
                else "running" if message.headers.get("step_interrupt", False)
                else "finished"
            )
            self.runner._task_response = TaskResponse(answer=message.payload,
                                                      success=not unsuccessful,
                                                      context=message.context,
                                                      id=self.runner.task.id,
                                                      time_cost=(time.time() - self.runner.start_time),
                                                      usage=self.runner.context.token_usage,
                                                      status=status,
                                                      semantic_status=semantic_status or "succeeded",
                                                      completion_reason=reason,
                                                      recoverable=execution_state.get("recoverable", True) if unsuccessful else None,
                                                      msg=(
                                                          "completion_contract_unsatisfied:"
                                                          + ",".join(completion.reason_codes)
                                                          if completion_blocked else reason
                                                      ),
                                                      failure_origin=(
                                                          TaskFailureOrigin.INFRASTRUCTURE.value
                                                          if completion_failure_is_infrastructure
                                                          else TaskFailureOrigin.TASK.value
                                                          if unsuccessful else None
                                                      ),
                                                      failure_code=(
                                                          completion_infrastructure_failure.get("failure_code")
                                                          if completion_failure_is_infrastructure
                                                          else "completion_contract_unsatisfied"
                                                          if completion_blocked else reason
                                                      ),
                                                      error_type=(
                                                          completion_infrastructure_failure.get("error_type")
                                                          if completion_failure_is_infrastructure
                                                          else None
                                                      ))

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
                                                      status=TaskStatusValue.CANCELLED,
                                                      semantic_status="incomplete",
                                                      completion_reason="cancelled",
                                                      recoverable=False,
                                                      failure_origin=TaskFailureOrigin.CANCELLED.value,
                                                      failure_code="cancelled")
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
                                                      status=TaskStatusValue.INTERRUPTED,
                                                      semantic_status="incomplete",
                                                      completion_reason="interrupted",
                                                      recoverable=False,
                                                      failure_origin=TaskFailureOrigin.CANCELLED.value,
                                                      failure_code="interrupted")
            await self.runner.stop()
            yield Message(payload=self.runner._task_response, session_id=message.session_id, headers=message.headers,
                          topic=TopicType.TASK_RESPONSE)
