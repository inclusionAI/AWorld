# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import traceback
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Type

from aworld.logs.util import logger
from ..config import AmniContextProcessorConfig
from aworld.core.common import Observation
from aworld.core.context.base import Context
from ..event import Event

class BaseContextProcessor(ABC):
    """内存处理器基类"""
    
    def __init__(self, config):
        self.config = config

    @abstractmethod
    async def process(self, context: Context, event: Event, **kwargs) -> Optional[Observation]:
        """处理消息"""
        pass


class ProcessorFactory:
    """处理器工厂类"""
    
    _processors: Dict[str, Type[BaseContextProcessor]] = {}
    
    @classmethod
    def register(cls, processor_type: str, processor_class: Type[BaseContextProcessor]):
        """注册处理器类型"""
        cls._processors[processor_type] = processor_class
        logger.info(f"Registered processor: {processor_type}")
    
    @classmethod
    def create(cls, processor_config: AmniContextProcessorConfig) -> Optional[BaseContextProcessor]:
        processor_type = processor_config.type
        if processor_type not in cls._processors:
            logger.warning(f"Unknown processor type: {processor_type}")
            return None
        
        try:
            processor = cls._processors[processor_type](processor_config=processor_config)
            return processor
        except Exception as e:
            logger.warn(f"Failed to create processor {processor_type}: {traceback.format_exc()}")
            return None
    
    @classmethod
    def list_all_types(cls) -> List[str]:
        """获取所有注册的处理器类型"""
        return list(cls._processors.keys())
    
    @classmethod
    def get_processor_class(cls, processor_type: str) -> Optional[Type[BaseContextProcessor]]:
        """根据类型获取处理器类"""
        return cls._processors.get(processor_type)


def memory_processor(processor_type: str):
    """
    装饰器：自动注册内存处理器到ProcessorFactory
    
    Usage:
        @memory_processor("swarm_based_memory_processor")
        class MyProcessor(BaseMemoryProcessor):
            pass
    """
    def decorator(cls: Type[BaseContextProcessor]):
        # 注册到ProcessorFactory
        ProcessorFactory.register(processor_type, cls)
        return cls
    return decorator
