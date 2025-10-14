# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from abc import ABC, abstractmethod
from typing import Optional, List

from ..base import Event, EventStatus


class BaseEventStorage(ABC):
    """事件存储基础抽象类"""
    
    @abstractmethod
    async def put(self, event: Event) -> bool:
        pass
    
    @abstractmethod
    async def add(self, event: Event) -> bool:
        pass
    
    @abstractmethod
    async def get_by_id(self, event_id: str) -> Optional[Event]:
        pass
    
    @abstractmethod
    async def get_by_type(self, event_type: str) -> List[Event]:
        pass
    
    @abstractmethod
    async def get_by_namespace(self, namespace: str) -> List[Event]:
        pass
    
    @abstractmethod
    async def list_events(self, limit: int = 100, offset: int = 0) -> List[Event]:
        pass
    
    @abstractmethod
    async def delete_by_id(self, event_id: str) -> bool:
        pass
    
    @abstractmethod
    async def count(self) -> int:
        pass
    
    @abstractmethod
    async def clear(self) -> bool:
        pass
    
    @abstractmethod
    async def update_status(self, event_id: str, status: EventStatus) -> bool:
        pass
    
    @abstractmethod
    async def get_by_status(self, status: EventStatus) -> List[Event]:
        pass
