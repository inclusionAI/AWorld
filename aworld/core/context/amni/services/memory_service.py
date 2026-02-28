# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Memory Service - Manages short-term and long-term memory operations.

This service abstracts memory management operations from ApplicationContext,
providing a clean interface for managing conversation history, facts, user profiles, and memory consolidation.
"""
import abc
from typing import Optional, List

from aworld.logs.util import logger
from aworld.memory.models import MemoryMessage, UserProfile, Fact
from aworld.core.context.amni.state.common import WorkingState


class IMemoryService(abc.ABC):
    """Interface for memory management operations."""
    
    # Short-term Memory Operations
    
    @abc.abstractmethod
    def add_history_message(self, memory_message: MemoryMessage, namespace: str = "default") -> None:
        """
        Add a memory message to the working state (short-term memory).
        
        Args:
            memory_message: The memory message to add
            namespace: Namespace for storage
        """
        pass
    
    @abc.abstractmethod
    def get_memory_messages(self, last_n: int = 100, namespace: str = "default") -> List[MemoryMessage]:
        """
        Get memory messages from MemoryFactory instance (persistent memory store).

        Args:
            last_n: Number of recent messages to retrieve
            namespace: Namespace (agent_id) for retrieval

        Returns:
            List of memory messages
        """
        pass
    
    # Long-term Memory Operations
    
    @abc.abstractmethod
    def add_fact(self, fact: Fact, namespace: str = "default", **kwargs) -> None:
        """
        Add a fact to working state (long-term memory).
        
        Args:
            fact: The fact to add
            namespace: Namespace for storage
            **kwargs: Additional arguments
        """
        pass
    
    @abc.abstractmethod
    def get_facts(self, namespace: str = "default", **kwargs) -> Optional[List[Fact]]:
        """
        Get facts from working state (long-term memory).
        
        Args:
            namespace: Namespace for retrieval
            **kwargs: Additional arguments
            
        Returns:
            List of facts, or None if working state doesn't exist
        """
        pass
    
    @abc.abstractmethod
    async def retrieval_facts(self, namespace: str = "default", **kwargs) -> Optional[List[Fact]]:
        """
        Retrieve facts from long-term memory storage.
        
        This method queries the persistent memory storage (not just working state)
        to retrieve relevant facts based on current task context.
        
        Args:
            namespace: Namespace for retrieval
            **kwargs: Additional arguments
            
        Returns:
            List of facts retrieved from memory storage
        """
        pass
    
    @abc.abstractmethod
    def get_user_profiles(self, namespace: str = "default") -> Optional[List[UserProfile]]:
        """
        Get user profiles from working state.
        
        Args:
            namespace: Namespace for retrieval
            
        Returns:
            List of user profiles, or None if working state doesn't exist
        """
        pass
    
    @abc.abstractmethod
    async def consolidation(self, namespace: str = "default") -> None:
        """
        Context consolidation: Extract and generate long-term memory from context.
        
        This enables the Agent to continuously learn user preferences and behavior patterns,
        thereby enhancing its understanding and overall capabilities.
        
        Args:
            namespace: Namespace for consolidation
        """
        pass


class MemoryService(IMemoryService):
    """
    Memory Service implementation.
    
    Manages both short-term memory (conversation history) and long-term memory
    (facts, user profiles) stored in working state and persistent memory storage.
    """
    
    def __init__(self, context):
        """
        Initialize MemoryService with ApplicationContext.
        
        Args:
            context: ApplicationContext instance that provides access to working state and memory
        """
        self._context = context
    
    def _get_working_state(self, namespace: str = "default") -> Optional[WorkingState]:
        """Get working state for the given namespace."""
        return self._context._get_working_state(namespace)
    
    # Short-term Memory Operations
    
    def add_history_message(self, memory_message: MemoryMessage, namespace: str = "default") -> None:
        """Add a memory message to the working state (short-term memory)."""
        self._get_working_state(namespace).history_messages.append(memory_message)
    
    def get_memory_messages(self, last_n: int = 100, namespace: str = "default") -> List[MemoryMessage]:
        """Get memory messages from MemoryFactory instance (persistent memory store)."""
        from aworld.memory.main import MemoryFactory

        memory = MemoryFactory.instance()
        filters = {"agent_id": namespace, "memory_type": "message"}
        ctx = self._context
        try:
            agent_memory_config = ctx.get_agent_memory_config(namespace)
        except Exception:
            agent_memory_config = None
        query_scope = (
            getattr(agent_memory_config, "history_scope", None) if agent_memory_config else None
        ) or "task"
        if query_scope == "user" and getattr(ctx, "user_id", None):
            filters["user_id"] = ctx.user_id
        elif query_scope == "session" and getattr(ctx, "session_id", None):
            filters["session_id"] = ctx.session_id
        elif getattr(ctx, "task_id", None):
            filters["task_id"] = ctx.task_id
        logger.info(f"get_memory_messages filters: {filters}, agent_memory_config: {agent_memory_config}")
        items = memory.get_last_n(last_n, filters=filters, agent_memory_config=agent_memory_config)
        return list(items) if items else []
    
    # Long-term Memory Operations
    
    def add_fact(self, fact: Fact, namespace: str = "default", **kwargs) -> None:
        """Add a fact to working state (long-term memory)."""
        # Add to root context's working state for long-term persistence
        self._context.root._get_working_state(namespace).facts.append(fact)
    
    def get_facts(self, namespace: str = "default", **kwargs) -> Optional[List[Fact]]:
        """Get facts from working state (long-term memory)."""
        working_state = self._get_working_state(namespace)
        if not working_state:
            return []
        return working_state.facts
    
    async def retrieval_facts(self, namespace: str = "default", **kwargs) -> Optional[List[Fact]]:
        """Retrieve facts from long-term memory storage."""
        import time
        from aworld.logs.util import logger
        from aworld.memory.main import MemoryFactory
        
        working_state = self._get_working_state(namespace)
        if not working_state:
            return []
        
        start_time = time.time()
        memory = MemoryFactory.instance()
        todo_info = await self._context.get_todo()
        current_task = "current_task: " + self._context.task_input
        concat_task_input = current_task + (todo_info if todo_info else "")
        facts = await memory.retrival_facts(
            user_id=self._context.user_id,
            user_input=concat_task_input,
            limit=10
        )
        logger.info(f"get_facts cost: {time.time() - start_time}")
        return facts
    
    def get_user_profiles(self, namespace: str = "default") -> Optional[List[UserProfile]]:
        """Get user profiles from working state."""
        working_state = self._get_working_state(namespace)
        if not working_state:
            return None
        return working_state.user_profiles
    
    async def consolidation(self, namespace: str = "default") -> None:
        """
        Context consolidation: Extract and generate long-term memory from context.
        
        Currently this is a placeholder. The actual implementation would trigger
        an event bus to process context consolidation asynchronously.
        """
        # TODO: Implement consolidation event bus
        # consolidation_event = EventBus.create_context_event(
        #     event_type=EventType.CONTEXT_CONSOLIDATION,
        #     context=self._context.deep_copy(),
        #     namespace=namespace
        # )
        # event_bus = await get_global_event_bus()
        # await event_bus.publish(consolidation_event)
        # logger.info(f"context#{self._context.task_id}[{namespace}] -> consolidation trigger")
        pass

