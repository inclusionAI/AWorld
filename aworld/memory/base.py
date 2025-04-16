import datetime
import hashlib
import uuid
from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    id: str = Field(description = "id")
    content: str = Field(description = "content")
    hash: str = Field(description = "content hash")
    created_at: Optional[str] = Field(None, description = "created at")
    updated_at: Optional[str] = Field(None, description = "updated at")
    metadata: dict = Field(description = "metadata, use to store additional information, such as user_id, agent_id, run_id, task_id, etc.")
    tags: list[str] = Field(description = "tags")
    version: int = Field(description = "version")

    def __init__(self, **data):
        # Set default values for optional fields
        if "id" not in data:
            data["id"] = str(uuid.uuid4())
        if "hash" not in data:
            data["hash"] = hashlib.md5(data.get("content", "").encode()).hexdigest()
        if "created_at" not in data:
            data["created_at"] = datetime.datetime.now().isoformat()
        if "updated_at" not in data:
            data["updated_at"] = data["created_at"]
        if "metadata" not in data:
            data["metadata"] = {}
        if "tags" not in data:
            data["tags"] = []
        if "version" not in data:
            data["version"] = 1

        super().__init__(**data)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryItem":
        """
        Create a MemoryItem instance from a dictionary.

        Args:
            data (dict): A dictionary containing the memory item data.

        Returns:
            MemoryItem: An instance of MemoryItem.
        """
        return cls(**data)

class MemoryStore(ABC):

    @abstractmethod
    def add(self, memory_item: MemoryItem):
        pass

    @abstractmethod
    def get(self, memory_id) -> Optional[MemoryItem]:
        pass

    @abstractmethod
    def get_all(self) -> list[MemoryItem]:
        pass

    @abstractmethod
    def update(self, memory_item: MemoryItem):
        pass

    @abstractmethod
    def delete(self, memory_id):
        pass

    @abstractmethod
    def retrieve(self, query, filters: dict) -> list[MemoryItem]:
        pass

    @abstractmethod
    def history(self, memory_id) -> list[MemoryItem]:
        pass
    

class MemoryBase(ABC):


    @abstractmethod
    def retrieve(self, query, filters: dict) -> list[MemoryItem]:
        """
        Retrieve a memory by ID.

        Args:
            query (str): query.
            filters (dict): filters.

        Returns:
            list: Retrieved memory.
        """
        pass


    @abstractmethod
    def get(self, memory_id) -> Optional[MemoryItem]:
        """
        get memory by ID.

        Args:
            memory_id (str): ID of the memory to retrieve.

        Returns:
            dict: Retrieved memory.
        """
        pass

    @abstractmethod
    def get_all(self) -> list[MemoryItem]:
        """
        List all memories.

        Returns:
            list: List of all memories.
        """
        pass

    @abstractmethod
    def add(self, memory_item: MemoryItem):
        """
        add memory

        Args:
            memory_item (MemoryItem): memory item.

        """
        pass

    @abstractmethod
    def update(self, memory_item: MemoryItem):
        """
        Update a memory by ID.

        Args:
            memory_item (MemoryItem): memory item.

        Returns:
            dict: Updated memory.
        """
        pass

    @abstractmethod
    def delete(self, memory_id):
        """
        Delete a memory by ID.

        Args:
            memory_id (str): ID of the memory to delete.
        """
        pass

    @abstractmethod
    def history(self, memory_id) -> list[MemoryItem]:
        """
        Get the history of changes for a memory by ID.

        Args:
            memory_id (str): ID of the memory to get history for.

        Returns:
            list: List of changes for the memory.
        """
        pass

class InMemoryMemoryStore(MemoryStore):

    def __init__(self):
        self.memory_items = {}

    def add(self, memory_item: MemoryItem):
        self.memory_items[memory_item.id] = memory_item

    def get(self, memory_id) -> Optional[MemoryItem]:
        return self.memory_items.get(memory_id)

    def get_all(self) -> list[MemoryItem]:
        return list(self.memory_items.values())
        

    def update(self, memory_item: MemoryItem):
        self.memory_items[memory_item.id] = memory_item

    def delete(self, memory_id):
        del self.memory_items[memory_id]

    def retrieve(self, query, filters: dict) -> list[MemoryItem]:
        return [memory_item for memory_item in self.memory_items.values() if memory_item.content.lower().find(query.lower()) != -1]

    def history(self, memory_id) -> list[MemoryItem]:
        return [memory_item for memory_item in self.memory_items.values() if memory_item.id == memory_id]
