# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import uuid
from typing import List, Optional, Tuple

from aworld.core.memory import MemoryItem, MemoryConfig,LongTermConfig
from aworld.models.llm import LLMModel
from .base import MemoryOrchestrator, MemoryProcessingTask
from ..models import LongTermExtractParams


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
        extract_param: LongTermExtractParams,
        longterm_config: LongTermConfig
    ) -> Tuple[bool, str]:
        # Check message count threshold
        if self.check_message_count_threshold(extract_param.memories, longterm_config):
            return True,"message_count"
            
        # Check content importance if enabled
        if longterm_config.trigger.enable_importance_trigger:
            if self.check_content_importance(extract_param.memories, longterm_config):
                return True,"content_importance"
        
        return False,"not_trigger"
    
    def create_memory_task(
        self, 
        extract_param: LongTermExtractParams,
        longterm_config: LongTermConfig
    ) -> Optional[MemoryProcessingTask]:
        """
        Create a memory processing task from the given memory items.
        
        Args:
            extract_param: Long-term extract parameters
            longterm_config: Long-term memory configuration settings
            
        Returns:
            Memory processing task
        """

        # Check if processing should be triggered
        should_process,reason = self.should_process_memory(
            extract_param,
            longterm_config=longterm_config
        )

        if not should_process:
            return None

        # create long-term memory task
        memory_task = MemoryProcessingTask(
            task_type=extract_param.extract_type,
            extract_params=extract_param
        )

        # Add metadata based on configuration
        memory_task.metadata.update({
            "trigger_reason": reason,
            'config_snapshot': {
                'message_threshold': longterm_config.trigger.message_count_threshold,
                'user_profile_extraction': longterm_config.extraction.enable_user_profile_extraction,
                'agent_experience_extraction': longterm_config.extraction.enable_agent_experience_extraction
            }
        })

        return memory_task
    
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
        recent_items = memory_items[-1:] if len(memory_items) > 1 else memory_items
        importance_keywords = longterm_config.trigger.importance_keywords
        
        for item in recent_items:
            content = item.content.lower()
            for keyword in importance_keywords:
                if keyword.lower() in content:
                    return True
        
        return False
