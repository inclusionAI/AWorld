import datetime
import hashlib
import uuid
from abc import ABC, abstractmethod
from typing import Optional, Any

from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    id: str = Field(description="id")
    content: Any = Field(description="content")
    hash: str = Field(description="content hash")
    created_at: Optional[str] = Field(None, description="created at")
    updated_at: Optional[str] = Field(None, description="updated at")
    metadata: dict = Field(
        description="metadata, use to store additional information, such as user_id, agent_id, run_id, task_id, etc.")
    tags: list[str] = Field(description="tags")
    version: int = Field(description="version")

    def __init__(self, **data):
        # Set default values for optional fields
        if "id" not in data:
            data["id"] = str(uuid.uuid4())
        if "hash" not in data:
            data["hash"] = hashlib.md5((data.get("content", "") or "").encode()).hexdigest()
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
        """Create a MemoryItem instance from a dictionary.

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
    def get_first(self) -> Optional[MemoryItem]:
        pass

    @abstractmethod
    def total_rounds(self) -> int:
        pass

    @abstractmethod
    def get_all(self) -> list[MemoryItem]:
        pass

    @abstractmethod
    def get_last_n(self, last_rounds) -> list[MemoryItem]:
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
        """Retrieve a memory by ID.

        Args:
            query (str): query.
            filters (dict): filters.

        Returns:
            list: Retrieved memory.
        """

    @abstractmethod
    def get(self, memory_id) -> Optional[MemoryItem]:
        """Get item in memory by ID.

        Args:
            memory_id (str): ID of the memory to retrieve.

        Returns:
            dict: Retrieved memory.
        """

    @abstractmethod
    def get_all(self) -> list[MemoryItem]:
        """List all items in memory store.

        Returns:
            list: List of all memories.
        """

    @abstractmethod
    def get_last_n(self, last_rounds) -> list[MemoryItem]:
        """get last_rounds memories.

        Returns:
            list: List of latest memories.
        """

    @abstractmethod
    def add(self, memory_item: MemoryItem):
        """Add memory in the memory store.


        Args:
            memory_item (MemoryItem): memory item.
        """

    @abstractmethod
    def update(self, memory_item: MemoryItem):
        """Update a memory by ID.

        Args:
            memory_item (MemoryItem): memory item.

        Returns:
            dict: Updated memory.
        """

    @abstractmethod
    def delete(self, memory_id):
        """Delete a memory by ID.

        Args:
            memory_id (str): ID of the memory to delete.
        """

    @abstractmethod
    def history(self, memory_id) -> list[MemoryItem]:
        """Get the history of changes for a memory by ID.

        Args:
            memory_id (str): ID of the memory to get history for.

        Returns:
            list: List of changes for the memory.
        """
