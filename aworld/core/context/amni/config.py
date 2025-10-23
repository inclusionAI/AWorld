# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import logging
import os
import queue
import threading
from enum import Enum
from logging.handlers import TimedRotatingFileHandler
from typing import Optional, List, Union, Dict, Any

from pydantic import BaseModel, Field

from aworld.config import ModelConfig
from aworld.config.conf import AgentMemoryConfig
from aworld.core.memory import MemoryConfig, MemoryLLMConfig, EmbeddingsConfig
from aworld.memory.db.sqlite import SQLiteMemoryStore
# from aworld.memory.db import SQLiteMemoryStore  # Temporarily commented out to avoid import errors
from aworld.memory.main import MemoryFactory
from .event.base import EventType
from .retrieval.base import RetrieverFactory
from .retrieval.graph.base import GraphDBConfig


class EventSubscriptionConfig(BaseModel):
    """Event subscription configuration"""
    event_types: Optional[List[str]] = Field(default_factory=list)  # None means subscribe to all event types
    exclude_event_types: Optional[List[str]] = Field(default_factory=list)  # Excluded event types
    namespaces: Optional[List[str]] = Field(default_factory=list)  # Subscribed namespaces
    exclude_namespaces: Optional[List[str]] = Field(default_factory=list)  # Excluded namespaces

    def should_process_event(self, event_type: str, namespace: str) -> bool:
        """Determine whether this event should be processed"""
        # Check if in exclude list
        if event_type in self.exclude_event_types:
            return False
        # If namespaces are configured, validate them
        if self.namespaces is not None and len(self.namespaces) > 0 and namespace not in self.namespaces:
            return False
        if namespace in self.exclude_namespaces:
            return False

        # Check event type filtering
        if self.event_types is not None and event_type not in self.event_types:
            return False

        return True


class BaseConfig(BaseModel):
    llm_model: Optional[ModelConfig] = None

class AmniContextProcessorConfig(BaseModel):
    name: Optional[str]
    type: Optional[str]
    pipeline: Optional[str]
    subscription: Optional[EventSubscriptionConfig] = Field(default_factory=EventSubscriptionConfig)
    is_async: Optional[bool] = False
    priority: Optional[int] = 0 # Lower numbers execute first

class BaseNeuronStrategyConfig(BaseModel):
    # Inherit from BaseModel to support Pydantic serialization
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, **data):
        super().__init__(**data)
    
    def __getitem__(self, key):
        return getattr(self, key)
    
    def __setitem__(self, key, value):
        setattr(self, key, value)
    
    def get(self, key, default=None):
        return getattr(self, key, default)


class NeuronStrategyConfig(BaseNeuronStrategyConfig):
    # Prompt configuration: init|append -> initialize only | append after generation
    prompt_augment_strategy: Optional[str] = Field(default="init")

class HumanNeuronStrategyConfig(NeuronStrategyConfig):
    mode: str = Field(description="Mode: block|wait")
    wait_time: int = Field(default=10, description="Wait time in seconds")



class AgentContextConfig(BaseModel):
    # System Prompt Augment
    enable_system_prompt_augment: bool = Field(default=False, description="enable_system_prompt_augment")
    neuron_names: Optional[list[str]] = Field(default_factory=list)
    neuron_config: Optional[Dict[str, NeuronStrategyConfig]] = Field(default_factory=list)

    # Context Reduce - Purge
    history_rounds: int = Field(default=100,
                                description="rounds of message msg; when the number of messages is greater than the history_rounds, the memory will be trimmed")

    # Context Reduce - Compress
    enable_summary: bool = Field(default=False,
                                 description="enable_summary use llm to create summary short-term memory")
    summary_model: Optional[str] = Field(default=None, description="short-term summary model")
    summary_rounds: Optional[int] = Field(default=5,
                                          description="rounds of message msg; when the number of messages is greater than the summary_rounds, the summary will be created")
    summary_context_length: Optional[int] = Field(default=40960,
                                                  description=" when the content length is greater than the summary_context_length, the summary will be created")

    # Context Offload
    tool_result_offload: bool = Field(default=False, description="tool result offload")
    tool_action_white_list: Optional[list[str]] = Field(default_factory=list, description="tool white list")
    tool_result_length_threshold: Optional[int] = Field(default=30000, description=" when the content length is greater than the tool_result_length_threshold, the tool result will be offloaded")

    # Context Retrival
    enable_chunk: bool = Field(default=False, description="enable_chunk")
    enable_full_text_index: bool = Field(default=False, description="enable_full_text")
    enable_semantic_index: bool = Field(default=False, description="enable_semantic_index")
    enable_rerank: bool = Field(default=False, description="enable_rerank")

    def to_memory_config(self) -> AgentMemoryConfig:
        return AgentMemoryConfig(
            history_rounds=self.history_rounds,
            enable_summary=self.enable_summary,
            summary_rounds=self.summary_rounds,
            summary_context_length=self.summary_context_length
        )


DEFAULT_AGENT_CONFIG = AgentContextConfig()
class AmniContextConfig(BaseModel):
    """AmniContext configs"""

    # agent config
    agent_config: Union[AgentContextConfig, Dict[str, AgentContextConfig]] = Field(default_factory=dict)

    # processor config
    processor_config: Optional[list[AmniContextProcessorConfig]] = Field(default_factory=list)

    # other config
    debug_mode: Optional[bool] = False

    def get_agent_context_config(self, namespace: str = "default") -> AgentContextConfig:
        if isinstance(self.agent_config, AgentContextConfig):
            return self.agent_config
        elif isinstance(self.agent_config, dict):
            return self.agent_config.get(namespace)
        else:
            return DEFAULT_AGENT_CONFIG

    def get_agent_memory_config(self, namespace: str = "default") -> AgentMemoryConfig:
        if isinstance(self.agent_config, AgentContextConfig):
            return self.agent_config.to_memory_config()
        elif isinstance(self.agent_config, dict):
            agent_context_config = self.agent_config.get(namespace)
            if isinstance(agent_context_config, AgentContextConfig):
                return agent_context_config.to_memory_config()
        return DEFAULT_AGENT_CONFIG.to_memory_config()

def init_middlewares(init_memory: bool = True, init_retriever: bool = True) -> None:

    # 1. Initialize memory
    if init_memory:
        MemoryFactory.init(
            custom_memory_store=SQLiteMemoryStore(db_path=os.getenv("DB_PATH", "./data/amni_context.db")),
            config=build_memory_config()
        )

    # 2. Initialize retriever
    if init_retriever:
        RetrieverFactory.init()

def build_memory_config():
    from aworld.core.memory import VectorDBConfig
    return MemoryConfig(
        provider="aworld",
        llm_config=MemoryLLMConfig(
            provider="openai",
            model_name=os.getenv("LLM_MODEL_NAME"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL")
        ),
        embedding_config=EmbeddingsConfig(
            base_url=os.getenv('EMBEDDING_BASE_URL'),
            api_key=os.getenv('EMBEDDING_API_KEY'),
            model_name=os.getenv('EMBEDDING_MODEL_NAME'),
            dimensions=int(os.getenv('EMBEDDING_MODEL_DIMENSIONS', '1024'))
        ),
        vector_store_config=VectorDBConfig(
            provider="chroma",
            config={
                "chroma_data_path": os.getenv('CHROMA_PATH', "./data/chroma_db"),
                "collection_name": "aworld_memory",
            }
        )
    )


def get_default_config() -> AmniContextConfig:
    return AmniContextConfig(
        processor_config=[
            AmniContextProcessorConfig(
                name="message_processor",
                type="pipeline_memory_processor",
                pipeline="extract_user_profile|save_memory",
                subscription=EventSubscriptionConfig(
                    event_types=[EventType.AGENT_RESULT],
                )
            ),
            # System prompt augmentation
            AmniContextProcessorConfig(
                name="augmented_system_prompt_to_memory",
                type="pipeline_memory_processor",
                pipeline="system_prompt_augment|save_memory",
                subscription=EventSubscriptionConfig(
                    event_types=[EventType.SYSTEM_PROMPT],
                )
            ),
            # Tool result offloading
            AmniContextProcessorConfig(
                name="tool_offload_save_memory",
                type="pipeline_memory_processor",
                pipeline="tool_result_offload|save_memory",
                subscription=EventSubscriptionConfig(
                    event_types=[EventType.TOOL_RESULT],
                ),
                priority=0
            )
        ]
    )


class AmniConfigLevel(Enum):

    # Basic version - user-programmable with intervention
    PILOT = "Pilot"

    # Advanced version - basic automation
    COPILOT = "CoPilot"

    # Premium version - intelligent automation
    NAVIGATOR = "Navigator"

CONTEXT_OFFLOAD_TOOL_NAME_WHITE = ["arxiv-server:load_article_to_context",
                                       "wiki-server:get_article_categories", "wiki-server:get_article_links",
                                       "ms-playwright:browser_snapshot", "ms-playwright:browser_navigate",
                                       "ms-playwright:browser_click", "ms-playwright:browser_type",
                                       "ms-playwright:browser_evaluate", "ms-playwright:browser_tab_select",
                                       "ms-playwright:browser_press_key", "ms-playwright:browser_wait_for"
                                       ]

class AmniConfigFactory:


    @staticmethod
    def create(level: Optional[AmniConfigLevel] = None) -> AmniContextConfig:
        if not level or level == AmniConfigLevel.PILOT or level == AmniConfigLevel.COPILOT:
            return get_default_config()
        elif level == AmniConfigLevel.NAVIGATOR:
            config = get_default_config()
            config.agent_config = AgentContextConfig(
                enable_system_prompt_augment=True,
                neuron_names= ["basic", "task", "work_dir", "todo", "action_info"],
                history_rounds= 100,
                enable_summary=True,
                summary_rounds= 30,
                summary_context_length= 40960,
                tool_result_offload= True,
                tool_action_white_list= CONTEXT_OFFLOAD_TOOL_NAME_WHITE,
                tool_result_length_threshold= 30000
            )
            return config
        raise ValueError(f"Unsupported level: {level}")

