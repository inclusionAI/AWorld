# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import time
from typing import AsyncGenerator, TYPE_CHECKING, Tuple

from env_channel import EnvChannelMessage

from aworld.core.common import TaskItem, Observation
from aworld.core.context.amni import get_context_manager, ContextManager, ApplicationContext, AmniContext
from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants, TopicType, BackgroundTaskMessage, AgentMessage
from aworld.core.task import TaskResponse, TaskStatusValue, Task, Runner
from aworld.events.util import send_message
from aworld.logs.util import logger
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryHumanMessage, MessageMetadata
from aworld.runner import Runners
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.hook.hooks import HookPoint

if TYPE_CHECKING:
    from aworld.runners.event_runner import TaskEventRunner


class BackgroundTaskHandler(DefaultHandler):
    """Handler for background task messages.
    
    This handler processes messages from background tasks that have completed execution.
    It handles the background task results and integrates them back into the main task flow.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, runner: 'TaskEventRunner'):
        super().__init__(runner)
        self.runner = runner
        self.hooks = {}
        if runner.task.hooks:
            for k, vals in runner.task.hooks.items():
                self.hooks[k] = []
                for v in vals:
                    from aworld.runners.hook.hook_factory import HookFactory
                    cls = HookFactory.get_class(v)
                    if cls:
                        self.hooks[k].append(cls)

    @classmethod
    def name(cls):
        return "_background_task_handler"

    def _should_trigger_agent(self, message: Message) -> bool:
        """Judge if the message should trigger an agent request immediately.
        
        Default implementation returns False. Subclasses can override this logic
        based on message content, headers or other criteria.
        """
        return  message.headers.get("trigger_agent", False)


@HandlerFactory.register(name=f'__{Constants.BACKGROUND_TASK}__')
class DefaultBackgroundTaskHandler(BackgroundTaskHandler):
    """Default handler for background task completion messages.
    
    Processes background task completion notifications and handles their results:
    - Success: Integrates background task results into parent task context
    - Failure: Handles errors and optionally triggers retry or fallback logic
    - Timeout/Cancellation: Handles abnormal termination scenarios
    """

    def is_valid_message(self, message: Message):
        """Validate if the message is a background task category message."""
        if message.category != Constants.BACKGROUND_TASK:
            return False
        return True

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        """Handle background task completion message.
        
        Supports two merge scenarios:
        1. Hot-Merge: Main task is still running, directly merge results
        2. Wake-up Merge: Main task completed, restore from checkpoint and merge
        
        Args:
            message: BackgroundTaskMessage containing the completed background task information
            
        Yields:
            Message: Follow-up messages based on background task result
        """
        task_flag = "sub" if self.runner.task.is_sub_task else "main"
        logger.info(
            f"[{self.name()}] {task_flag} task {self.runner.task.id} received background task message: {message}, payload: {message.payload}")

        headers = {"context": message.context}
        topic = message.topic
        
        # Extract background task information
        bg_task_msg: BackgroundTaskMessage = message
        bg_task_id = bg_task_msg.background_task_id if isinstance(bg_task_msg, BackgroundTaskMessage) else None
        parent_task_id = bg_task_msg.parent_task_id if isinstance(bg_task_msg, BackgroundTaskMessage) else None

        if parent_task_id == self.runner.task.id:
            # The background task triggered by the current task has returned its result
            logger.info(f"[{self.name()}] Current task {self.runner.task.id} is the parent of background task {bg_task_id}")
            await self._hot_merge(bg_task_id, message.payload, message)
            if False:
                yield message
            return
        else:
            # set message.task_id to parent_task_id
            ret_msg = BackgroundTaskMessage(
                background_task_id=bg_task_id,
                parent_task_id=parent_task_id,
                payload=message.payload,
                headers={
                    "context": Context(task_id=parent_task_id)
                }
            )
            await send_message(ret_msg)

    async def _hot_merge(self, bg_task_id: str, bg_task_response: TaskResponse, message: Message):
        """Hot-Merge: Main task is running, directly merge background task result.
        
        In this scenario:
        - Main task continues execution while background task runs
        - When background task completes, merge result into current main task state
        - No checkpoint restore needed
        
        Args:
            bg_task_id: Background task ID
            bg_task_response: Background task execution result
            message: Original completion message
        """
        logger.info(f"[{self.name()}] Executing Hot-Merge for background task {bg_task_id}")
        await self._merge_by_topic(message)


        logger.info(
            f"[{self.name()}] Hot-Merge completed for background task {bg_task_id}. "
            f"Result merged into main task {self.runner.task.id}"
        )

    async def _merge_by_topic(self, message: Message):
        topic = message.topic
        headers = {"context": message.context}
        if not message or not message.payload:
            logger.error(f"[{self.name()}] Empty background task message: {message}")
            return
        # Extract background task information
        bg_task_msg: BackgroundTaskMessage = message
        bg_task_context = message.context
        bg_task_id = bg_task_msg.background_task_id
        parent_task_id = bg_task_msg.parent_task_id
        agent_id = bg_task_msg.agent_id
        agent_name = bg_task_msg.agent_name

        memory = MemoryFactory.instance()
        try:
            session_id = message.context.get_task().session_id
            user_id = message.context.get_task().user_id
            data = message.payload
            content = data
            if isinstance(data, Tuple) and isinstance(data[0], Observation):
                data = data[0]
                content = data.content
            elif isinstance(data, Observation):
                content = data.content
            elif isinstance(data, EnvChannelMessage):
                data = data.message
                if not agent_id:
                    agent_id = data.get('env_content', {}).get('agent_id')
                content = data
            elif isinstance(data, dict):
                if not agent_id:
                    agent_id = data.get('env_content', {}).get('agent_id')
                content = data
            elif isinstance(data, str):
                content = data
            elif isinstance(message.payload, TaskResponse):
                bg_task_response: TaskResponse = message.payload
                # Merge background task context into main context
                if bg_task_response.context:
                    logger.debug(f"[{self.name()}] Merging background task context into main context")
                    self.runner.context.merge_sub_context(bg_task_response.context)
                content = bg_task_response.answer or bg_task_response.msg
            else:
                logger.warning(f"[{self.name()}] Unsupported background tool result: {data}.")
            try:
                if self._should_trigger_agent(message):
                    # Directly trigger an agent request
                    trigger_msg = AgentMessage(
                        payload=Observation(content=content),
                        receiver=agent_id,
                        session_id=session_id,
                        headers=headers
                    )
                    await send_message(trigger_msg)
                    logger.info(f"[{self.name()}] Directly triggered agent request for agent {agent_id}")
                else:
                    # Add to agent's memory as pending
                    pending_msg = MemoryHumanMessage(
                        content=str(content),
                        metadata=MessageMetadata(
                            session_id=session_id,
                            user_id=user_id,
                            task_id=parent_task_id,
                            agent_id=agent_id,
                            agent_name=agent_id,
                        ),
                        memory_type="pending"
                    )
                    await memory.add(pending_msg)
                    logger.info(f"[{self.name()}] Added background task result to memory for agent {agent_id}")
            except Exception as e:
                logger.error(f"[{self.name()}] Failed to process background task result: {e}")
                raise e
        except Exception as e:
            logger.warn(f"[{self.name()}] Failed to merge background task message: {e}", exc_info=True)



