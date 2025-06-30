# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Dict, Any, Optional, TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from aworld.core.memory import MemoryItem, MemoryStore, LongTermConfig
from aworld.models.llm import LLMModel
from aworld.memory.models import UserProfile, AgentExperience, LongTermExtractParams


class MemoryProcessingTask(BaseModel):
    """
    Represents a memory processing task containing information needed for long-term memory processing.
    
    Args:
        memory_task_id: Task identifier
        task_type: Task type
        extract_params: Long-term extract parameters
        created_at: Creation timestamp
        finished_at: Finished timestamp
    """
    memory_task_id: str = Field(default=str(uuid.uuid4()), description="Memory task identifier")
    task_type: Literal['user_profile', 'agent_experience'] = Field(..., description="Memory task type")
    extract_params: LongTermExtractParams = Field(description="Long-term extract parameters")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Creation timestamp")
    finished_at: str = Field(default=None, description="Finished timestamp")

class ProcessingResult:
    """
    Represents the result of memory processing operation.
    
    Args:
        task_id: Task identifier
        application_id: Application identifier
        success: Whether the processing was successful
    """
    
    def __init__(self, task_id: str, application_id: str, success: bool) -> None:
        self.task_id = task_id
        self.application_id = application_id
        self.success = success
        self.user_profiles: List[UserProfile] = []
        self.agent_experiences: List[AgentExperience] = []
        self.processing_time: float = 0.0
        self.error_message: str = ""


class MemoryOrchestrator(ABC):
    """
    Abstract base class for memory orchestrator that determines when and how to process memories.
    Responsible for evaluating trigger conditions and creating processing tasks.
    """
    
    def __init__(self, llm_instance: LLMModel) -> None:
        """
        Initialize the memory orchestrator.
        
        Args:
            llm_instance: LLM model instance for processing
        """
        self.llm_instance = llm_instance
    
    @abstractmethod
    def should_process_memory(
            self,
            extract_param: LongTermExtractParams,
            longterm_config: LongTermConfig
    ) -> bool:
        """
        Determine whether the given memory items should be processed for long-term storage.
        
        Args:
            extract_param: Long-term extract parameters
            longterm_config: Long-term memory configuration settings
            
        Returns:
            True if processing should be triggered, False otherwise
        """
        pass
    
    @abstractmethod
    def create_memory_task(
        self,
        extract_param: LongTermExtractParams,
        longterm_config: "LongTermConfig"
    ) -> Optional[MemoryProcessingTask]:
        """
        Create a memory processing task from the given memory items.
        
        Args:
            extract_param: Long-term extract parameters
            longterm_config: Long-term memory configuration settings
            
        Returns:
            Memory processing task
        """
        pass
    
    @abstractmethod
    def check_message_count_threshold(self, memory_items: List[MemoryItem], longterm_config: "LongTermConfig") -> bool:
        """
        Check if the message count threshold is reached.
        
        Args:
            memory_items: List of memory items to check
            longterm_config: Long-term memory configuration settings
            
        Returns:
            True if threshold is reached, False otherwise
        """
        pass
    
    @abstractmethod
    def check_content_importance(self, memory_items: List[MemoryItem], longterm_config: "LongTermConfig") -> bool:
        """
        Check if the content importance threshold is reached.
        
        Args:
            memory_items: List of memory items to check
            longterm_config: Long-term memory configuration settings
            
        Returns:
            True if content is important enough, False otherwise
        """
        pass


class MemoryGungnir(ABC):
    """
    Abstract base class for memory processing engine (Gungnir - the eternal spear of memory).
    Responsible for extracting and processing long-term memories from short-term memory items.
    """
    
    def __init__(self, llm_instance: LLMModel, embedding_model: Optional[Any] = None) -> None:
        """
        Initialize the memory processing engine.
        
        Args:
            llm_instance: LLM model instance for processing
            embedding_model: Embedding model for semantic operations
        """
        self.llm_instance = llm_instance
        self.embedding_model = embedding_model
    
    @abstractmethod
    def process_memory_task(
        self, 
        task: MemoryProcessingTask, 
        long_term_memory: MemoryStore,
        longterm_config: "LongTermConfig"
    ) -> ProcessingResult:
        """
        Process a memory task and extract long-term memories.
        
        Args:
            task: Memory processing task to execute
            long_term_memory: Long-term memory store for context retrieval
            longterm_config: Long-term memory configuration settings
            
        Returns:
            Processing result containing extracted memories
        """
        pass
    
    @abstractmethod
    def extract_user_profiles(
        self, 
        messages: List[MemoryItem], 
        application_id: str, 
        agent_id: str, 
        user_id: str,
        longterm_config: "LongTermConfig"
    ) -> List[UserProfile]:
        """
        Extract user profiles from memory items.
        
        Args:
            messages: List of memory items to analyze
            application_id: Application identifier
            agent_id: Agent identifier
            user_id: User identifier
            longterm_config: Long-term memory configuration settings
            
        Returns:
            List of extracted user profiles
        """
        pass
    
    @abstractmethod
    def extract_agent_experiences(
        self, 
        messages: List[MemoryItem], 
        application_id: str, 
        agent_id: str,
        longterm_config: "LongTermConfig"
    ) -> List[AgentExperience]:
        """
        Extract agent experiences from memory items.
        
        Args:
            messages: List of memory items to analyze
            application_id: Application identifier
            agent_id: Agent identifier
            longterm_config: Long-term memory configuration settings
            
        Returns:
            List of extracted agent experiences
        """
        pass
    
    @abstractmethod
    def retrieve_similar_memories(
        self, 
        query: str, 
        long_term_memory: MemoryStore, 
        application_id: str,
        longterm_config: "LongTermConfig"
    ) -> List[MemoryItem]:
        """
        Retrieve similar memories from long-term storage for context.
        
        Args:
            query: Query string for similarity search
            long_term_memory: Long-term memory store
            application_id: Application identifier for filtering
            longterm_config: Long-term memory configuration settings
            
        Returns:
            List of similar memory items
        """
        pass
    
    @abstractmethod
    def call_llm_for_user_profile_extraction(self, messages: List[MemoryItem], longterm_config: "LongTermConfig") -> str:
        """
        Call LLM to extract user profile information from messages.
        
        Args:
            messages: List of memory items to analyze
            longterm_config: Long-term memory configuration settings
            
        Returns:
            LLM response containing user profile information
        """
        pass
    
    @abstractmethod
    def call_llm_for_agent_experience_extraction(self, messages: List[MemoryItem], longterm_config: "LongTermConfig") -> str:
        """
        Call LLM to extract agent experience information from messages.
        
        Args:
            messages: List of memory items to analyze
            longterm_config: Long-term memory configuration settings
            
        Returns:
            LLM response containing agent experience information
        """
        pass
    
    @abstractmethod
    def parse_user_profiles_from_llm_response(
        self, 
        response: str, 
        application_id: str, 
        user_id: str,
        longterm_config: "LongTermConfig"
    ) -> List[UserProfile]:
        """
        Parse user profiles from LLM response.
        
        Args:
            response: LLM response string
            application_id: Application identifier
            user_id: User identifier
            longterm_config: Long-term memory configuration settings
            
        Returns:
            List of parsed user profiles
        """
        pass
    
    @abstractmethod
    def parse_agent_experiences_from_llm_response(
        self, 
        response: str, 
        application_id: str, 
        agent_id: str,
        longterm_config: "LongTermConfig"
    ) -> List[AgentExperience]:
        """
        Parse agent experiences from LLM response.
        
        Args:
            response: LLM response string
            application_id: Application identifier
            agent_id: Agent identifier
            longterm_config: Long-term memory configuration settings
            
        Returns:
            List of parsed agent experiences
        """
        pass 