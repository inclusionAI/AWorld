# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import logging
from typing import Optional, Callable

from .base import Event

class EventHandler:
    """Base class for event handlers"""
    
    def __init__(self, name: str, handler_func: Callable, priority: int = 0):
        self.name = name
        self.handler_func = handler_func
        self.priority = priority
        self.is_active = True
    
    async def handle(self, event: Event) -> Optional[Event]:
        """Handle event"""
        if not self.is_active:
            return None
        
        try:
            if asyncio.iscoroutinefunction(self.handler_func):
                result = await self.handler_func(event)
            else:
                result = self.handler_func(event)
            
            if isinstance(result, Event):
                return result
            return None
        except Exception as e:
            import traceback
            logging.error(f"Handler {self.name} failed: {e}, traceback is {traceback.format_exc()}")
            return None
