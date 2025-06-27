# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import uuid
from typing import List

from aworld.core.memory import MemoryItem, MemoryConfig,LongTermConfig
from aworld.models.llm import LLMModel
from .base import MemoryOrchestrator, MemoryProcessingTask


class SimpleMemoryOrchestrator(MemoryOrchestrator):
    """
    Simple implementation of MemoryOrchestrator that provides basic memory processing decisions.
    This orchestrator evaluates trigger conditions and creates processing tasks based on configuration.
    """
    
    def __init__(self, llm_instance: LLMModel) -> None:
        """
        Initialize the simple memory orchestrator.
        
        Args:
            llm_instance: LLM model instance for processing
        """
        super().__init__(llm_instance)
    
    def should_process_memory(
        self, 
        memory_items: List[MemoryItem], 
        application_id: str, 
        agent_id: str, 
        user_id: str, 
        session_id: str,
        longterm_config: LongTermConfig
    ) -> bool:
        """
        Determine whether the given memory items should be processed for long-term storage.
        
        Args:
            memory_items: List of memory items to evaluate
            application_id: Application identifier
            agent_id: Agent identifier
            user_id: User identifier
            session_id: Session identifier
            longterm_config: Long-term memory configuration settings
            
        Returns:
            True if processing should be triggered, False otherwise
        """
        # Check message count threshold
        if self.check_message_count_threshold(memory_items, longterm_config):
            return True
            
        # Check content importance if enabled
        if longterm_config.trigger.enable_importance_trigger:
            if self.check_content_importance(memory_items, longterm_config):
                return True
        
        return False
    
    def create_memory_task(
        self, 
        memory_items: List[MemoryItem], 
        application_id: str, 
        agent_id: str, 
        user_id: str, 
        session_id: str, 
        task_id: str,
        longterm_config: LongTermConfig
    ) -> MemoryProcessingTask:
        """
        Create a memory processing task from the given memory items.
        
        Args:
            memory_items: List of memory items to process
            application_id: Application identifier
            agent_id: Agent identifier
            user_id: User identifier
            session_id: Session identifier
            task_id: Task identifier
            longterm_config: Long-term memory configuration settings
            
        Returns:
            Memory processing task
        """
        if not task_id:
            task_id = str(uuid.uuid4())
            
        task = MemoryProcessingTask(
            task_id=task_id,
            application_id=application_id,
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
            memories=memory_items
        )
        
        # Add metadata based on configuration
        task.metadata.update({
            'trigger_reason': self._get_trigger_reason(memory_items, longterm_config),
            'config_snapshot': {
                'message_threshold': longterm_config.trigger.message_count_threshold,
                'user_profile_extraction': longterm_config.extraction.enable_user_profile_extraction,
                'agent_experience_extraction': longterm_config.extraction.enable_agent_experience_extraction
            }
        })
        
        return task
    
    def check_message_count_threshold(self, memory_items: List[MemoryItem], longterm_config: LongTermConfig) -> bool:
        """
        Check if the message count threshold is reached.
        
        Args:
            memory_items: List of memory items to check
            longterm_config: Long-term memory configuration settings
            
        Returns:
            True if threshold is reached, False otherwise
        """
        return len(memory_items) >= longterm_config.trigger.message_count_threshold
    
    def check_content_importance(self, memory_items: List[MemoryItem], longterm_config: LongTermConfig) -> bool:
        """
        Check if the content importance threshold is reached.
        
        Args:
            memory_items: List of memory items to check
            longterm_config: Long-term memory configuration settings
            
        Returns:
            True if content is important enough, False otherwise
        """
        if not longterm_config.trigger.enable_importance_trigger:
            return False
            
        # Check for importance keywords in recent messages
        recent_items = memory_items[-5:] if len(memory_items) > 5 else memory_items
        importance_keywords = longterm_config.trigger.importance_keywords
        
        for item in recent_items:
            content = item.content.lower()
            for keyword in importance_keywords:
                if keyword.lower() in content:
                    return True
        
        return False
    
    def _get_trigger_reason(self, memory_items: List[MemoryItem], longterm_config: LongTermConfig) -> str:
        """
        Get the reason why processing was triggered.
        
        Args:
            memory_items: List of memory items
            longterm_config: Long-term memory configuration settings
            
        Returns:
            String describing the trigger reason
        """
        reasons = []
        
        if self.check_message_count_threshold(memory_items, longterm_config):
            reasons.append(f"message_count_threshold({longterm_config.trigger.message_count_threshold})")
            
        if self.check_content_importance(memory_items, longterm_config):
            reasons.append("content_importance")
            
        return ", ".join(reasons) if reasons else "unknown" 