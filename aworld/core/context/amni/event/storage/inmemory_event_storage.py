# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import traceback
from collections import defaultdict
from typing import Optional, List, Dict

from ..base import Event, EventStatus
from .base_event_storage import BaseEventStorage


class InMemoryEventStorage(BaseEventStorage):
    """In-memory event storage implementation"""
    
    def __init__(self):
        self._events: Dict[str, Event] = {}
        self._events_by_type: Dict[str, List[str]] = defaultdict(list)
        self._events_by_namespace: Dict[str, List[str]] = defaultdict(list)
        self._events_by_status: Dict[str, List[str]] = defaultdict(list)
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._lock = asyncio.Lock()
    
    async def put(self, event: Event) -> bool:
        try:
            async with self._lock:
                self._events[event.event_id] = event
                
                event_type_key = event.event_type if event.event_type else "unknown"
                if event.event_id not in self._events_by_type[event_type_key]:
                    self._events_by_type[event_type_key].append(event.event_id)
                
                if event.event_id not in self._events_by_namespace[event.namespace]:
                    self._events_by_namespace[event.namespace].append(event.event_id)
                
                status_key = event.status
                if event.event_id not in self._events_by_status[status_key]:
                    self._events_by_status[status_key].append(event.event_id)
                
                await self._event_queue.put(event)
                
                return True
        except Exception as e:
            print(f"Error storing event {event.event_id}: {e}, traceback: {traceback.format_exc()}")
            return False
    
    async def add(self, event: Event) -> bool:
        return await self.put(event)
    
    async def get_by_id(self, event_id: str) -> Optional[Event]:
        async with self._lock:
            return self._events.get(event_id)
    
    async def get_by_type(self, event_type: str) -> List[Event]:
        async with self._lock:
            event_ids = self._events_by_type.get(event_type, [])
            return [self._events[event_id] for event_id in event_ids if event_id in self._events]
    
    async def get_by_namespace(self, namespace: str) -> List[Event]:
        async with self._lock:
            event_ids = self._events_by_namespace.get(namespace, [])
            return [self._events[event_id] for event_id in event_ids if event_id in self._events]
    
    async def list_events(self, limit: int = 100, offset: int = 0) -> List[Event]:
        async with self._lock:
            all_events = list(self._events.values())
            all_events.sort(key=lambda x: x.timestamp, reverse=True)
            return all_events[offset:offset + limit]
    
    async def delete_by_id(self, event_id: str) -> bool:
        try:
            async with self._lock:
                if event_id not in self._events:
                    return False
                
                event = self._events[event_id]
                
                del self._events[event_id]
                
                event_type_key = event.event_type if event.event_type else "unknown"
                if event_id in self._events_by_type[event_type_key]:
                    self._events_by_type[event_type_key].remove(event_id)
                
                if event_id in self._events_by_namespace[event.namespace]:
                    self._events_by_namespace[event.namespace].remove(event_id)
                
                status_key = event.status
                if event_id in self._events_by_status[status_key]:
                    self._events_by_status[status_key].remove(event_id)
                
                return True
        except Exception as e:
            print(f"Error deleting event {event_id}: {e}")
            return False
    
    async def count(self) -> int:
        async with self._lock:
            return len(self._events)
    
    async def clear(self) -> bool:
        try:
            async with self._lock:
                self._events.clear()
                self._events_by_type.clear()
                self._events_by_namespace.clear()
                self._events_by_status.clear()
                
                while not self._event_queue.empty():
                    try:
                        self._event_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                
                return True
        except Exception as e:
            print(f"Error clearing events: {e}")
            return False
    
    async def get_queue_size(self) -> int:
        return self._event_queue.qsize()
    
    async def get_from_queue(self, timeout: Optional[float] = None) -> Optional[Event]:
        try:
            if timeout:
                return await asyncio.wait_for(self._event_queue.get(), timeout=timeout)
            else:
                return await self._event_queue.get()
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            print(f"Error getting from queue: {e}")
            return None
    
    async def update_status(self, event_id: str, status: EventStatus) -> bool:
        try:
            async with self._lock:
                if event_id not in self._events:
                    return False
                
                event = self._events[event_id]
                old_status = event.status
                
                event.status = status
                
                old_status_key = old_status
                if event_id in self._events_by_status[old_status_key]:
                    self._events_by_status[old_status_key].remove(event_id)
                
                new_status_key = status
                if event_id not in self._events_by_status[new_status_key]:
                    self._events_by_status[new_status_key].append(event_id)
                
                return True
        except Exception as e:
            print(f"Error updating status for event {event_id}: {e}")
            return False
    
    async def get_by_status(self, status: str) -> List[Event]:
        async with self._lock:
            status_key = status
            event_ids = self._events_by_status.get(status_key, [])
            return [self._events[event_id] for event_id in event_ids if event_id in self._events]
