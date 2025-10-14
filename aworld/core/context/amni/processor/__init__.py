"""
Processor模块 - 包含所有处理器相关的类和工厂
"""

from .base import BaseArtifactProcessor
from .memory_processor import PipelineMemoryProcessor
from .processor_factory import BaseContextProcessor, ProcessorFactory

# 基础导出列表
__all__ = [
    # 基础类
    "ProcessorFactory",
    "BaseContextProcessor",
    "BaseArtifactProcessor",
    "PipelineMemoryProcessor",
    "memory_processor",
]
