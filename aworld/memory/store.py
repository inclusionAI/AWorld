"""Storage contract independent of configuration and model orchestration."""

from abc import ABC, abstractmethod
from typing import Optional

from aworld.memory.models import MemoryItem


class MemoryStore(ABC):
    """
    Memory store interface for messages history
    """

    @abstractmethod
    def add(self, memory_item: MemoryItem):
        pass

    @abstractmethod
    def get(self, memory_id) -> Optional[MemoryItem]:
        pass

    @abstractmethod
    def get_first(self, filters: dict = None) -> Optional[MemoryItem]:
        pass

    @abstractmethod
    def total_rounds(self, filters: dict = None) -> int:
        pass

    @abstractmethod
    def get_all(self, filters: dict = None) -> list[MemoryItem]:
        pass

    @abstractmethod
    def get_last_n(self, last_rounds, filters: dict = None) -> list[MemoryItem]:
        pass

    @abstractmethod
    def update(self, memory_item: MemoryItem):
        pass

    @abstractmethod
    def delete(self, memory_id):
        pass

    @abstractmethod
    def delete_items(self, message_types: list[str], session_id: str, task_id: str, filters: dict = None):
        pass

    @abstractmethod
    def history(self, memory_id) -> list[MemoryItem] | None:
        pass
