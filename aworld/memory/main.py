from typing import Optional
from aworld.memory.base import MemoryBase, MemoryItem, MemoryStore, InMemoryMemoryStore


class Memory(MemoryBase):

    def __init__(self, memory_store: MemoryStore):
        self.memory_store = memory_store


    @classmethod
    def from_config(cls, config: dict) -> "Memory":
        """
        Initialize a Memory instance from a configuration dictionary.

        Args:
            config (dict): Configuration dictionary.

        Returns:
            Memory: Memory instance.
        """
        if config.get("memory_store") == "inmemory":    
            return Memory(InMemoryMemoryStore())
        else:
            raise ValueError(f"Invalid memory store type: {config.get('memory_store')}")
        

    def add(self, memory_item: MemoryItem):
        self.memory_store.add(memory_item)

    def update(self, memory_item: MemoryItem):
        self.memory_store.update(memory_item)

    def delete(self, memory_id):
        self.memory_store.delete(memory_id)

    def get(self, memory_id) -> Optional[MemoryItem]:
        return self.memory_store.get(memory_id)

    def get_all(self) -> list[MemoryItem]:
        return self.memory_store.get_all()

    def retrieve(self, query, filters: dict) -> list[MemoryItem]:
        return self.memory_store.retrieve(query, filters)

    def history(self, memory_id) -> list[MemoryItem]:
        return self.memory_store.history(memory_id)