# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import traceback
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Type

from aworld.core.common import Observation
from aworld.core.context.base import Context
from aworld.logs.util import logger
from ..config import AmniContextProcessorConfig
from ..event import ContextMessagePayload


class BaseContextProcessor(ABC):
    """Base class for memory processors"""
    
    def __init__(self, config):
        self.config = config

    @abstractmethod
    async def process(self, context: Context, event: ContextMessagePayload, **kwargs) -> Optional[Observation]:
        """Process messages"""
        pass


class ProcessorFactory:
    """Processor factory class"""
    
    _processors: Dict[str, Type[BaseContextProcessor]] = {}
    
    @classmethod
    def register(cls, processor_type: str, processor_class: Type[BaseContextProcessor]):
        """Register processor type"""
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
        """Get all registered processor types"""
        return list(cls._processors.keys())
    
    @classmethod
    def get_processor_class(cls, processor_type: str) -> Optional[Type[BaseContextProcessor]]:
        """Get processor class by type"""
        return cls._processors.get(processor_type)


def memory_processor(processor_type: str):
    """
    Decorator: Automatically register memory processor to ProcessorFactory
    
    Usage:
        @memory_processor("swarm_based_memory_processor")
        class MyProcessor(BaseMemoryProcessor):
            pass
    """
    def decorator(cls: Type[BaseContextProcessor]):
        # Register to ProcessorFactory
        ProcessorFactory.register(processor_type, cls)
        return cls
    return decorator
