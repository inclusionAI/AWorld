# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import traceback
from typing import Dict, List, Optional, Type

from aworld.logs.util import logger
from .base import BaseOp


class OpFactory:
    """Operation factory class"""

    _ops: Dict[str, Type[BaseOp]] = {}

    @classmethod
    def register(cls, op_name: str, op_class: Type[BaseOp]):
        """Register operation type"""
        if op_name in cls._ops:
            logger.warning(f"Memory op '{op_name}' is already registered, skipping duplicate registration")
            return
        cls._ops[op_name] = op_class
        logger.info(f"Registered memory op: {op_name}")

    @classmethod
    def create(cls, op_name: str, **kwargs) -> Optional[BaseOp]:
        """Create operation instance"""
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
        """Get all registered operation types"""
        return list(cls._ops.keys())

    @classmethod
    def get_op_class(cls, op_name: str) -> Optional[Type[BaseOp]]:
        """Get operation class by name"""
        return cls._ops.get(op_name)


def memory_op(op_name: str):
    """
    Decorator: Automatically register memory operation to OpFactory
    
    Usage:
        @memory_op("set_query")
        class SetQueryOp(BaseOp):
            pass
    """

    def decorator(cls: Type[BaseOp]):
        # Register to OpFactory
        OpFactory.register(op_name, cls)
        return cls

    return decorator
