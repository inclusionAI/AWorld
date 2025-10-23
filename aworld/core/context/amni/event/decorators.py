# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from typing import List, Union

from .base import EventType


def event_handler(event_types: Union[List[EventType], str] = None, priority: int = 10):
    """
    Event handler annotation decorator
    
    Args:
        event_types: List of event types to handle or "*" to handle all event types
        priority: Handler priority, smaller number means higher priority
    """
    def decorator(cls_or_func):
        # Mark as event handler
        if hasattr(cls_or_func, '__event_handler__'):
            # If already an event handler, update configuration
            cls_or_func.__event_types__ = event_types
            cls_or_func.__event_priority__ = priority
        else:
            # New event handler
            cls_or_func.__event_handler__ = True
            cls_or_func.__event_types__ = event_types
            cls_or_func.__event_priority__ = priority
            
        return cls_or_func
    return decorator
