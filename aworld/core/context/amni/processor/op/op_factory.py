# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import traceback
from typing import Dict, List, Optional, Type

from aworld.logs.util import logger
from .base import BaseOp


class OpFactory:
    """操作工厂类"""

    _ops: Dict[str, Type[BaseOp]] = {}

    @classmethod
    def register(cls, op_name: str, op_class: Type[BaseOp]):
        """注册操作类型"""
        if op_name in cls._ops:
            logger.warning(f"Memory op '{op_name}' is already registered, skipping duplicate registration")
            return
        cls._ops[op_name] = op_class
        logger.info(f"Registered memory op: {op_name}")

    @classmethod
    def create(cls, op_name: str, **kwargs) -> Optional[BaseOp]:
        """创建操作实例"""
        if op_name not in cls._ops:
            logger.warning(f"Unknown memory op: {op_name}")
            return None

        try:
            return cls._ops[op_name](op_name, **kwargs)
        except Exception as e:
            logger.warn(f"Failed to create memory op {op_name}: {traceback.format_exc()}")
            return None

    @classmethod
    def list_all_ops(cls) -> List[str]:
        """获取所有注册的操作类型"""
        return list(cls._ops.keys())

    @classmethod
    def get_op_class(cls, op_name: str) -> Optional[Type[BaseOp]]:
        """根据名称获取操作类"""
        return cls._ops.get(op_name)


def memory_op(op_name: str):
    """
    装饰器：自动注册内存操作到OpFactory
    
    Usage:
        @memory_op("set_query")
        class SetQueryOp(BaseOp):
            pass
    """

    def decorator(cls: Type[BaseOp]):
        # 注册到OpFactory
        OpFactory.register(op_name, cls)
        return cls

    return decorator
