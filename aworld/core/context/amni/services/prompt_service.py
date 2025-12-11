# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Prompt Service - Manages prompt generation and formatting from context.

This service abstracts prompt generation operations from ApplicationContext,
providing a clean interface for building prompts from context templates and managing prompt events.
"""
import abc
from typing import Optional, Dict, Any, List

from aworld.core.context.amni.prompt.prompt_ext import ContextPromptTemplate


class IPromptService(abc.ABC):
    """Interface for prompt generation operations."""
    
    @abc.abstractmethod
    async def format_prompt(self, template: str, context: 'ApplicationContext' = None, **kwargs) -> str:
        """
        Format a prompt template with context variables.
        
        Args:
            template: Prompt template string with {{variable}} placeholders
            context: ApplicationContext instance for variable resolution
            **kwargs: Additional variables to pass to template
            
        Returns:
            Formatted prompt string
        """
        pass
    
    @abc.abstractmethod
    def create_template(self, template: str, **kwargs) -> ContextPromptTemplate:
        """
        Create a ContextPromptTemplate instance.
        
        Args:
            template: Prompt template string
            **kwargs: Additional template configuration options
            
        Returns:
            ContextPromptTemplate instance
        """
        pass
    
    @abc.abstractmethod
    async def pub_and_wait_system_prompt_event(self, system_prompt: str, user_query: str, 
                                              agent_id: str, agent_name: str, 
                                              namespace: str = "default") -> None:
        """
        Publish and wait for system prompt event.
        
        This method publishes a system prompt event and waits for processing results.
        
        Args:
            system_prompt: System prompt content
            user_query: User query string
            agent_id: Agent identifier
            agent_name: Agent name
            namespace: Namespace for the event
        """
        pass
    
    @abc.abstractmethod
    async def pub_and_wait_tool_result_event(self, tool_result: Any, tool_call_id: str,
                                            agent_id: str, agent_name: str,
                                            namespace: str = "default") -> None:
        """
        Publish and wait for tool result event.
        
        This method publishes a tool result event and waits for processing results.
        
        Args:
            tool_result: Tool execution result
            tool_call_id: Tool call identifier
            agent_id: Agent identifier
            agent_name: Agent name
            namespace: Namespace for the event
        """
        pass


class PromptService(IPromptService):
    """
    Prompt Service implementation.
    
    Manages prompt generation from templates, context variable resolution,
    and prompt-related event publishing.
    """
    
    def __init__(self, context):
        """
        Initialize PromptService with ApplicationContext.
        
        Args:
            context: ApplicationContext instance that provides access to context data
        """
        self._context = context
    
    async def format_prompt(self, template: str, context: 'ApplicationContext' = None, **kwargs) -> str:
        """Format a prompt template with context variables."""
        prompt_template = self.create_template(template)
        # Use provided context or default to service's context
        target_context = context if context is not None else self._context
        return await prompt_template.async_format(context=target_context, **kwargs)
    
    def create_template(self, template: str, **kwargs) -> ContextPromptTemplate:
        """Create a ContextPromptTemplate instance."""
        return ContextPromptTemplate(template=template, **kwargs)
    
    async def pub_and_wait_system_prompt_event(self, system_prompt: str, user_query: str, 
                                              agent_id: str, agent_name: str, 
                                              namespace: str = "default") -> None:
        """Publish and wait for system prompt event."""
        import traceback
        from aworld.logs.util import logger
        from ..payload import SystemPromptMessagePayload
        from ...event.base import ContextMessage, Constants, TopicType
        from aworld.events.util import send_message_with_future
        
        logger.info(f"ApplicationContext|pub_and_wait_system_prompt_event|start|{namespace}|{agent_id}")
        payload = SystemPromptMessagePayload(
            context=self._context,
            system_prompt=system_prompt,
            user_query=user_query,
            agent_id=agent_id,
            agent_name=agent_name,
            event_type=TopicType.SYSTEM_PROMPT,
            namespace=namespace
        )
        message = ContextMessage(
            category=Constants.CONTEXT,
            payload=payload,
            sender=None,
            receiver=None,
            session_id=self._context.session_id,
            topic=TopicType.SYSTEM_PROMPT,
            headers={"context": self._context}
        )
        # Send via message system by default
        try:
            future = await send_message_with_future(message)
            results = await future.wait(timeout=300)
            if not results:
                logger.warning(f"context write task failed: {message}")
        except Exception as e:
            logger.warn(f"context write task failed: {traceback.format_exc()}")
    
    async def pub_and_wait_tool_result_event(self, tool_result: Any, tool_call_id: str,
                                            agent_id: str, agent_name: str,
                                            namespace: str = "default") -> None:
        """Publish and wait for tool result event."""
        import traceback
        from aworld.logs.util import logger
        from ..payload import ToolResultMessagePayload
        from ...event.base import ContextMessage, Constants, TopicType
        from aworld.events.util import send_message_with_future
        
        logger.info(f"ApplicationContext|pub_and_wait_tool_result_event|start|{namespace}|{agent_id}")
        payload = ToolResultMessagePayload(
            event_type=TopicType.TOOL_RESULT,
            tool_result=tool_result,
            context=self._context,
            tool_call_id=tool_call_id,
            agent_id=agent_id,
            agent_name=agent_name,
            namespace=namespace
        )
        message = ContextMessage(
            category=Constants.CONTEXT,
            payload=payload,
            sender=None,
            receiver=None,
            session_id=self._context.session_id,
            topic=TopicType.SYSTEM_PROMPT,
            headers={"context": self._context}
        )
        # Send via message system by default
        try:
            future = await send_message_with_future(message)
            results = await future.wait(timeout=300)
            if not results:
                logger.warning(f"context write task failed: {message}")
        except Exception as e:
            logger.warn(f"context write task failed: {traceback.format_exc()}")

