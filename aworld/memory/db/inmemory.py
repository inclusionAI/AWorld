# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""In-memory storage independent of model-backed memory orchestration."""

from typing import Optional

from aworld.memory.models import MemoryItem
from aworld.memory.store import MemoryStore


class InMemoryMemoryStore(MemoryStore):
    def __init__(self):
        self.memory_items = []

    def add(self, memory_item: MemoryItem):
        self.memory_items.append(memory_item)

    def get(self, memory_id) -> Optional[MemoryItem]:
        return next((item for item in self.memory_items if item.id == memory_id), None)

    def get_first(self, filters: dict = None) -> Optional[MemoryItem]:
        """Get the first memory item."""
        filtered_items = self.get_all(filters)
        if len(filtered_items) == 0:
            return None
        return filtered_items[0]

    def total_rounds(self, filters: dict = None) -> int:
        """Get the total number of rounds."""
        return len(self.get_all(filters))

    def get_all(self, filters: dict = None) -> list[MemoryItem]:
        """Filter memory items based on filters."""
        filtered_items = [item for item in self.memory_items if self._filter_memory_item(item, filters)]
        return filtered_items

    def _filter_memory_item(self, memory_item: MemoryItem, filters: dict = None) -> bool:
        if memory_item.deleted:
            return False
        if filters is None:
            return True
        if filters.get('application_id') is not None:
            if memory_item.application_id is None:
                return False
            if memory_item.application_id != filters['application_id']:
                return False
        if filters.get('user_id') is not None:
            if memory_item.user_id is None:
                return False
            if memory_item.user_id != filters['user_id']:
                return False
        if filters.get('agent_id') is not None:
            if memory_item.agent_id is None:
                return False
            if memory_item.agent_id != filters['agent_id']:
                return False
        if filters.get('agent_name') is not None:
            if memory_item.agent_id is None:
                return False
            if memory_item.agent_id != filters['agent_id']:
                return False
        if filters.get('task_id') is not None:
            if memory_item.task_id is None:
                return False
            if memory_item.task_id != filters['task_id']:
                return False
        if filters.get('session_id') is not None:
            if memory_item.session_id is None:
                return False
            if memory_item.session_id != filters['session_id']:
                return False
        if filters.get('tool_call_id') is not None:
            if memory_item.metadata.get("tool_call_id") is None:
                return False
            if memory_item.metadata.get("tool_call_id") != filters['tool_call_id']:
                return False
        if filters.get('memory_type') is not None:
            if memory_item.memory_type is None:
                return False
            elif isinstance(filters['memory_type'], list) and memory_item.memory_type not in filters['memory_type']:
                return False
            elif isinstance(filters['memory_type'], str) and memory_item.memory_type != filters['memory_type']:
                return False
        return True

    def get_last_n(self, last_rounds, filters: dict = None) -> list[MemoryItem]:
        return self.get_all(filters=filters)[-last_rounds:]

    def update(self, memory_item: MemoryItem):
        for index, item in enumerate(self.memory_items):
            if item.id == memory_item.id:
                self.memory_items[index] = memory_item
                break

    def delete(self, memory_id):
        exists = self.get(memory_id)
        if exists:
            exists.deleted = True

    def delete_items(self, message_types: list[str], session_id: str, task_id: str, filters: dict = None):
        for item in self.memory_items:
            if item.memory_type in message_types and item.session_id == session_id and item.task_id == task_id:
                item.deleted = True

    def history(self, memory_id) -> list[MemoryItem] | None:
        exists = self.get(memory_id)
        if exists:
            return exists.histories
        return None
