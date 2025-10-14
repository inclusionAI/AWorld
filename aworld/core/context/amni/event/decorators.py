# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from typing import List, Union

from .base import EventType


def event_handler(event_types: Union[List[EventType], str] = None, priority: int = 10):
    """
    事件处理器注解装饰器
    
    Args:
        event_types: 要处理的事件类型列表或 "*" 表示处理所有事件类型
        priority: 处理器优先级，数字越小优先级越高
    """
    def decorator(cls_or_func):
        # 标记为事件处理器
        if hasattr(cls_or_func, '__event_handler__'):
            # 如果已经是事件处理器，更新配置
            cls_or_func.__event_types__ = event_types
            cls_or_func.__event_priority__ = priority
        else:
            # 新的事件处理器
            cls_or_func.__event_handler__ = True
            cls_or_func.__event_types__ = event_types
            cls_or_func.__event_priority__ = priority
            
        return cls_or_func
    return decorator
