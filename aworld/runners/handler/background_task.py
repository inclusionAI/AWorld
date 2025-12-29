# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import time
from typing import AsyncGenerator, TYPE_CHECKING, Tuple

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
            # 当前任务所触发的后台任务返回结果了
            logger.info(f"[{self.name()}] Current task {self.runner.task.id} is the parent of background task {bg_task_id}")
            await self._hot_merge(bg_task_id, message.payload, message)
            if False:
                yield message
            # # 唤醒 Agent 继续执行
            # if self.runner.agent_oriented:
            #     # 构造一个 Observation 包含后台任务结果
            #     obs = Observation(
            #         content=f"Background task {bg_task_id} completed. Result: {message.payload.answer if hasattr(message.payload, 'answer') else message.payload}",
            #         description=f"Background task completion notification"
            #     )
                
            #     agents = self.runner.swarm.communicate_agent
            #     if not isinstance(agents, list):
            #         agents = [agents]
                
            #     for agent in agents:
            #         yield AgentMessage(
            #             payload=obs,
            #             sender=self.name(),
            #             receiver=agent.id(),
            #             session_id=self.runner.context.session_id,
            #             headers={'context': self.runner.context}
            #         )
            return

        context_mng = get_context_manager()
        ckpt = context_mng.aget_checkpoint(self.runner.context.session_id)
        # paren_task_id不等于当前task_id，说明当前是后台任务的runner运行流程，需要把消息发回到parent task
        # todo: get parent_task's task_status
        main_task_status = await self.get_task_status(parent_task_id, message)
        is_main_task_running = main_task_status == TaskStatusValue.RUNNING
        # 如果parent_task的task_status是running，说明parent_task还在运行，直接send_message发给parent task
        if is_main_task_running:
            ret_msg = BackgroundTaskMessage(
                background_task_id=bg_task_id,
                parent_task_id=parent_task_id,
                payload=message.payload,
                headers={
                    "context": Context(task_id=parent_task_id)
                }
            )
            await send_message(ret_msg)
        else:
            logger.info(f"[{self.name()}] Parent task {parent_task_id} is not running, new task")
            # 父任务已经结束，需要新建一个runner，唤醒parent task
            # todo: 怎么唤醒原来的task
            await self.restore_parent_task(parent_task_id, message)

    async def get_task_status(self, task_id: str, message: Message) -> str:
        """Get the status of a task.

        Args:
            task_id: Task ID
            message: Message

        Returns:
            TaskStatusValue: Task status
        """
        if isinstance(message.context, ApplicationContext):
            cur_context: ApplicationContext = message.context
            if cur_context.parent and cur_context.parent.task_id == task_id:
                return cur_context.parent.task_status
        else:
            # todo: get parent_task's task_status
            return TaskStatusValue.RUNNING
            
        # # Try to get from checkpoint if context doesn't have it
        # try:
        #     context_mng = get_context_manager()
        #     checkpoint = await context_mng.aget_checkpoint(message.session_id)
        #     if checkpoint and "task_state" in checkpoint.values:
        #         status = checkpoint.values["task_state"].get("status")
        #         if status:
        #             return status
        # except Exception as e:
        #     logger.warning(f"[{self.name()}] Failed to get parent task status from checkpoint: {e}")
            
        # return TaskStatusValue.RUNNING

    async def restore_parent_task(self, parent_task_id: str, message: Message):
        """Restore parent task from checkpoint and merge background task result.

        In this scenario:
        - Main task completed and entered sleep state waiting for background task
        - Main task state saved to checkpoint
        - When background task completes:
          1. Restore main task state from checkpoint
          2. Merge background task result
          3. Resume main task execution

        Args:
            parent_task_id: Parent task ID
            message: Original completion message
        """
        logger.info(f"[{self.name()}] Restoring parent task {parent_task_id} from checkpoint")
        
        # 1. 从 checkpoint 恢复主任务状态
        context_manager = get_context_manager()
        session_id = message.session_id
        main_context = await context_manager.build_context_from_checkpoint(session_id)
        if not main_context:
            logger.error(f"[{self.name()}] Failed to restore parent task {parent_task_id}: no checkpoint found")
            return

        # 2. 合并 background task 结果
        # 注意：此处合并到 restored context 中，而不是当前 runner 的 context
        main_context.merge_sub_context(message.context)
        
        # 获取主任务对象
        main_task = main_context.get_task()
        if not main_task:
            logger.error(f"[{self.name()}] No task object found in restored context for parent task {parent_task_id}")
            return

        # 3. 准备唤醒消息
        # 构造一个发往主任务的 BackgroundTaskMessage，携带合并后的 context
        bg_task_msg: BackgroundTaskMessage = message
        bg_task_id = bg_task_msg.background_task_id if hasattr(bg_task_msg, 'background_task_id') else None
        
        resume_msg = BackgroundTaskMessage(
            background_task_id=bg_task_id,
            parent_task_id=parent_task_id,
            payload=message.payload,
            sender=message.sender,
            session_id=session_id,
            headers={"context": main_context}
        )

        # 4. 创建新的 Runner 并恢复运行
        from aworld.runners.utils import choose_runners
        # 更新任务状态为运行中
        main_task.task_status = TaskStatusValue.RUNNING
        
        runners = await choose_runners([main_task])
        if runners:
            runner = runners[0]
            # 将唤醒消息加入初始消息列表，确保 Runner 启动后第一个处理它
            if hasattr(runner, 'init_messages'):
                # 清除原来的 init_messages（避免重复处理初始问题），仅保留唤醒消息
                runner.init_messages = [resume_msg]
            
            # 在后台异步启动 Runner，不阻塞当前后台任务的处理流程
            asyncio.create_task(runner.run())
            logger.info(f"[{self.name()}] Successfully triggered resume for parent task {parent_task_id}")
        else:
            logger.error(f"[{self.name()}] Failed to create runner for parent task {parent_task_id}")


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
    
    async def _wakeup_merge(self, bg_task_id: str, bg_task_response: TaskResponse, message: Message) -> bool:
        """Wake-up Merge: Main task completed, restore from checkpoint and merge.
        
        In this scenario:
        - Main task entered sleep state waiting for background task
        - Main task state saved to checkpoint
        - When background task completes:
          1. Restore main task state from checkpoint
          2. Merge background task result
          3. Resume main task execution
        
        Args:
            bg_task_id: Background task ID
            bg_task_response: Background task execution result
            message: Original completion message
            
        Returns:
            bool: True if wake-up merge succeeded, False if not supported/failed
        """
        logger.info(f"[{self.name()}] Executing Wake-up Merge for background task {bg_task_id}")
        
        try:
            # 1. Check if context supports checkpoint operations
            if not hasattr(self.runner.context, 'aget_checkpoint'):
                logger.warning(f"[{self.name()}] Context does not support checkpoint operations")
                return False
            
            session_id = self.runner.context.session_id
            
            # 2. Restore main task state from checkpoint
            logger.debug(f"[{self.name()}] Restoring main task state from checkpoint for session {session_id}")
            
            # Get checkpoint
            checkpoint = await self.runner.context.aget_checkpoint(session_id)
            if not checkpoint:
                logger.warning(f"[{self.name()}] No checkpoint found for session {session_id}")
                return False
            
            # Build context from checkpoint
            if hasattr(self.runner.context, 'build_context_from_checkpoint'):
                restored_context = await self.runner.context.build_context_from_checkpoint(session_id)
                if restored_context:
                    logger.info(f"[{self.name()}] Successfully restored context from checkpoint")
                    # Update runner's context with restored one
                    self.runner.context = restored_context
                else:
                    logger.warning(f"[{self.name()}] Failed to build context from checkpoint")
                    return False
            
            # 3. Merge background task result into restored context
            logger.debug(f"[{self.name()}] Merging background task result into restored context")
            if bg_task_response.context:
                self.runner.context.merge_sub_context(bg_task_response.context)
            
            # 4. Store background task result
            if 'background_task_results' not in self.runner.context.context_info:
                self.runner.context.context_info['background_task_results'] = {}
            
            self.runner.context.context_info['background_task_results'][bg_task_id] = {
                'answer': bg_task_response.answer,
                'success': True,
                'time_cost': bg_task_response.time_cost,
                'usage': bg_task_response.usage,
                'context': bg_task_response.context,
                'merge_type': 'wakeup_merge'
            }
            
            # 5. Update background task status
            if 'background_tasks_status' not in self.runner.context.context_info:
                self.runner.context.context_info['background_tasks_status'] = {}
            
            self.runner.context.context_info['background_tasks_status'][bg_task_id] = {
                'status': bg_task_response.status,
                'completed_at': time.time(),
                'restored_from_checkpoint': True
            }
            
            # 6. Resume main task execution
            # Note: The actual resume logic depends on how the runner is structured
            # This might trigger a task continuation message
            logger.info(
                f"[{self.name()}] Wake-up Merge completed for background task {bg_task_id}. "
                f"Main task {self.runner.task.id} state restored and ready to resume"
            )
            
            # Update task status to running if it was in a sleep/waiting state
            if self.runner.task.task_status in [TaskStatusValue.SUCCESS, TaskStatusValue.INIT]:
                self.runner.task.task_status = TaskStatusValue.RUNNING
                logger.debug(f"[{self.name()}] Updated main task status to RUNNING")
            
            return True
            
        except Exception as e:
            logger.error(f"[{self.name()}] Wake-up merge failed: {e}", exc_info=True)
            return False

    async def _merge_by_topic(self, message: Message):
        topic = message.topic
        headers = {"context": message.context}
        # Extract background task information
        bg_task_msg: BackgroundTaskMessage = message
        bg_task_context = message.context
        bg_task_id = bg_task_msg.background_task_id if isinstance(bg_task_msg, BackgroundTaskMessage) else None
        parent_task_id = bg_task_msg.parent_task_id if isinstance(bg_task_msg, BackgroundTaskMessage) else None
        agent_id = bg_task_msg.agent_id if isinstance(bg_task_msg, BackgroundTaskMessage) else None
        agent_name = bg_task_msg.agent_name if isinstance(bg_task_msg, BackgroundTaskMessage) else None

        memory = MemoryFactory.instance()
        try:
            if topic == TopicType.BACKGROUND_TOOL_COMPLETE:
                session_id = message.context.get_task().session_id
                user_id = message.context.get_task().user_id
                data = message.payload
                if isinstance(data, Tuple) and isinstance(data[0], Observation):
                    data = data[0]
                if isinstance(data, Observation):
                    pending_msg = MemoryHumanMessage(
                        content=data.content,
                        metadata=MessageMetadata(
                            session_id=session_id,
                            user_id=user_id,
                            task_id=parent_task_id,
                            agent_id=agent_id,
                            agent_name=agent_name,
                        ),
                        memory_type="pending"
                    )
                    await memory.add(pending_msg)
                else:
                    logger.warning(f"[{self.name()}] Unsupported background tool result: {data}")
            elif isinstance(message.payload, TaskResponse):
                bg_task_response: TaskResponse = message.payload
                # 1. Merge background task context into main context
                if bg_task_response.context:
                    logger.debug(f"[{self.name()}] Merging background task context into main context")
                    self.runner.context.merge_sub_context(bg_task_response.context)

                # 2. Store background task result for access by main task
                if 'background_task_results' not in self.runner.context.context_info:
                    self.runner.context.context_info['background_task_results'] = {}

                self.runner.context.context_info['background_task_results'][bg_task_id] = {
                    'answer': bg_task_response.answer,
                    'success': True,
                    'time_cost': bg_task_response.time_cost,
                    'usage': bg_task_response.usage,
                    'context': bg_task_response.context,
                    'merge_type': 'hot_merge'
                }

                # 3. Update background task status in context
                if 'background_tasks_status' not in self.runner.context.context_info:
                    self.runner.context.context_info['background_tasks_status'] = {}

                self.runner.context.context_info['background_tasks_status'][bg_task_id] = {
                    'status': bg_task_response.status,
                    'completed_at': time.time()
                }

        except Exception as e:
            logger.warn(f"[{self.name()}] Failed to merge background task message: {e}", exc_info=True)
        logger.info(f"[{self.name()}] Merged background task message: {message}")


