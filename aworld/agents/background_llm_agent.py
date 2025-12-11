# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import uuid
from typing import List, Dict, Any

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger


class BackgroundAgent(Agent):
    """Agent that runs policy in background and returns immediately.
    
    This agent executes the original async_policy in a background task,
    allowing the main flow to continue without waiting for completion.
    The task is created via build_sub_context with task_type='background',
    and when completed, it will be merged back to parent context.
    
    Example:
        >>> background_agent = BackgroundAgent(name="background_worker", ...)
        >>> # When called, returns immediately with task_id
        >>> result = await background_agent.async_policy(observation, info, message)
    """

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        """Execute policy in background and return immediately with task_id.
        
        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.
            message: Event message containing context.
            **kwargs: Additional keyword arguments.
        
        Returns:
            ActionModel sequence with a message indicating the task has started.
        """
        if message is None or message.context is None:
            raise ValueError("Message and context are required for BackgroundAgent")
        
        context = message.context
        task_id = f"background_task_{uuid.uuid4().hex[:8]}"
        
        logger.info(f"🚀 BackgroundAgent {self.id()} creating background task: {task_id}")
        
        # Build sub context for background task
        task_content = observation.content if hasattr(observation, 'content') else str(observation)
        sub_context = await context.build_sub_context(
            sub_task_content=task_content,
            sub_task_id=task_id,
            task_type='background'
        )
        
        # Create background task to run original async_policy
        async def background_task():
            """Background task wrapper that runs original policy and merges result."""
            try:
                logger.info(f"📋 BackgroundAgent {self.id()} task {task_id} started")
                
                # Create a new message with sub_context for the background task
                background_message = Message(
                    payload=observation,
                    sender=message.sender,
                    receiver=self.id(),
                    context=sub_context,
                    session_id=sub_context.session_id
                )
                
                # Call parent's async_policy in the sub context
                result = await super(BackgroundAgent, self).async_policy(
                    observation, info, background_message, **kwargs
                )
                
                # Merge sub context back to parent when task completes
                if sub_context.parent:
                    sub_context.parent.merge_sub_context(sub_context)
                    logger.info(f"✅ BackgroundAgent {self.id()} task {task_id} merged to parent context")
                
                return result
            except Exception as e:
                logger.error(f"❌ BackgroundAgent {self.id()} task {task_id} failed: {e}")
                # Still merge to update status to FAILED
                if sub_context.parent:
                    sub_context.parent.merge_sub_context(sub_context)
                raise
        
        # Create and start background task
        asyncio.create_task(background_task())
        
        # Return immediate response
        response_message = f"Task#{task_id} start running, you can check sub_task_list for status"
        
        return [ActionModel(
            agent_name=self.id(),
            policy_info=response_message,
            params={'task_id': task_id, 'status': 'running'}
        )]
