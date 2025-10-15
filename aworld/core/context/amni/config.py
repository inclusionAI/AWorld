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
# from aworld.memory.db import SQLiteMemoryStore  # 暂时注释掉，避免导入错误
from aworld.memory.main import MemoryFactory
from .event.base import EventType
from .retrieval.base import RetrieverFactory
from .retrieval.graph.base import GraphDBConfig


class EventSubscriptionConfig(BaseModel):
    """事件订阅配置"""
    event_types: Optional[List[str]] = Field(default_factory=list)  # None 表示订阅所有事件类型
    exclude_event_types: Optional[List[str]] = Field(default_factory=list)  # 排除的事件类型
    namespaces: Optional[List[str]] = Field(default_factory=list)  # 订阅的命名空间
    exclude_namespaces: Optional[List[str]] = Field(default_factory=list)  # 排除的命名空间

    def should_process_event(self, event_type: str, namespace: str) -> bool:
        """判断是否应该处理该事件"""
        # 检查是否在排除列表中
        if event_type in self.exclude_event_types:
            return False
        # 如果配置了命名空间，则进行校验
        if self.namespaces is not None and len(self.namespaces) > 0 and namespace not in self.namespaces:
            return False
        if namespace in self.exclude_namespaces:
            return False

        # 检查事件类型过滤
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
    priority: Optional[int] = 0 # 数字越小的优先执行

class BaseNeuronStrategyConfig(BaseModel):
    # 继承BaseModel来支持Pydantic序列化
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
    # 提示词配置 init|append -> 仅初始化|生成后追加
    prompt_augment_strategy: Optional[str] = Field(default="init")

class HumanNeuronStrategyConfig(NeuronStrategyConfig):
    mode: str = Field(description="模式, block|wait")
    wait_time: int = Field(default=10, description="等待时间, 单位: 秒")



class AgentContextConfig(BaseModel):
    # System Prompt Augment
    neuron_names: Optional[list[str]] = Field(default_factory=list)
    neuron_config: Optional[Dict[str, NeuronStrategyConfig]] = Field(default_factory=list)


    # Context Purge
    history_rounds: int = Field(default=100,
                                description="rounds of message msg; when the number of messages is greater than the history_rounds, the memory will be trimmed")

    # Context Compress
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
    tool_result_length_threshold: Optional[int] = Field(default=40960, description=" when the content length is greater than the tool_result_length_threshold, the tool result will be offloaded")



class AmniContextConfig(BaseModel):
    """AmniContext configs"""

    retrival_index_type_list: Optional[list[str]] = Field(default_factory=list)
    agent_config: Union[AgentContextConfig, Dict[str, AgentContextConfig]] = Field(default_factory=dict)

    processor_config: Optional[list[AmniContextProcessorConfig]] = Field(default_factory=list)
    # neuron_config removed - neurons are now registered via decorators in neuron_factory
    # other config
    debug_mode: Optional[bool] = False
    log_level: Optional[str] = "INFO"

    def get_agent_memory_config(self, namespace: str = "default") -> AgentMemoryConfig:
        if isinstance(self.agent_config, AgentContextConfig):
            return AgentMemoryConfig(
                history_rounds=self.agent_config.history_rounds,
                enable_summary=self.agent_config.enable_summary,
                summary_rounds=self.agent_config.summary_rounds,
                summary_context_length=self.agent_config.summary_context_length
            )
        elif isinstance(self.agent_config, list):
            agent_context_config = self.agent_config.get(namespace)
            if isinstance(agent_context_config, AgentContextConfig):
                return AgentMemoryConfig(
                    history_rounds=agent_context_config.history_rounds,
                    enable_summary=agent_context_config.enable_summary,
                    summary_rounds=agent_context_config.summary_rounds,
                    summary_context_length=agent_context_config.summary_context_length
                )
        return AgentMemoryConfig()

def init_middlewares(init_memory: bool = True, init_retriever: bool = True) -> None:

    ## 1. init memory
    if init_memory:
        MemoryFactory.init(
            custom_memory_store=SQLiteMemoryStore(db_path=os.getenv("DB_PATH", "./data/amni_context.db")),
            config=build_memory_config()
        )

    ## 2. init retriever
    if init_retriever:
        RetrieverFactory.init()

    graph_db_config=GraphDBConfig(
        provider="pg",
        config={
            "uri": os.getenv('GRAPH_STORE_URI'),
            "username": os.getenv('GRAPH_STORE_USERNAME'),
            "password": os.getenv('GRAPH_STORE_PASSWORD'),
            "port": os.getenv("GRAPH_STORE_PORT"),
            "database": os.getenv("GRAPH_STORE_DATABASE")
        }
    ),


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

DEFAULT_CONFIG: Optional[AmniContextConfig] = None
def get_amnicontext_config() -> AmniContextConfig:
    global DEFAULT_CONFIG

    if DEFAULT_CONFIG is None:
        # 默认配置
        DEFAULT_CONFIG = AmniContextConfig(
            processor_config=[
                AmniContextProcessorConfig(
                    name="message_processor",
                    type="pipeline_memory_processor",
                    pipeline="extract_user_profile|save_memory",
                    subscription=EventSubscriptionConfig(
                        event_types=[EventType.AGENT_RESULT],
                    )
                ),
                # 系统提示词增强
                AmniContextProcessorConfig(
                    name="augmented_system_prompt_to_memory",
                    type="pipeline_memory_processor",
                    pipeline="system_prompt_augment|save_memory",
                    subscription=EventSubscriptionConfig(
                        event_types=[EventType.SYSTEM_PROMPT],
                    )
                ),
                # 工具结果卸载
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
    return DEFAULT_CONFIG


class AmniConfigLevel(Enum):

    # 基础版本 用户介入可编程的
    PILOT = "Pilot"

    # 进阶版本 基础的自动化
    COPILOT = "CoPilot"

    # 高级版本 智能的自动化
    NAVIGATOR = "Navigator"

class AmniConfigFactory:

    @staticmethod
    def create(level: Optional[AmniConfigLevel] = None) -> AmniContextConfig:
        if not level or level == AmniConfigLevel.PILOT or level == AmniConfigLevel.COPILOT:
            return get_amnicontext_config()
        elif level == AmniConfigLevel.NAVIGATOR:
            return get_amnicontext_config()
        raise ValueError(f"Unsupported level: {level}")

