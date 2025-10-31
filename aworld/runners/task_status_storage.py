# coding: utf-8
import abc
import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from pydantic import BaseModel

from aworld.config import StorageConfig
from aworld.core.singleton import InheritanceSingleton
from aworld.core.storage.base import Storage
from aworld.core.storage.data import Data, DataBlock
from aworld.core.storage.condition import Condition
from aworld.logs.util import logger


class TaskStatus:
    """Task status constants."""
    INIT = "init"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    RESUMED = "resumed"
    TIMEOUT = "timeout"



class TaskStatusData(BaseModel):
    """Task status data structure for storage.
    
    Attributes:
        task_id: Unique task identifier
        status: Current task status
        reason: Optional reason for status change
        updated_at: Timestamp of last update
    """
    task_id: str = field(default=None)
    status: str = field(default=TaskStatus.INIT)
    reason: Optional[str] = field(default=None)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        """Set id to task_id for consistency."""
        if self.task_id:
            self.id = self.task_id


class TaskStatusStore:
    """Base class for task status storage using Storage abstraction.
    
    This implementation uses the Storage abstraction layer, allowing for
    pluggable backends (in-memory, Redis, SQLite, etc.).
    """

    def __init__(self, storage: Storage[Data]):
        """Initialize with a Storage instance.
        
        Args:
            storage: Storage instance for persisting task status data
        """
        self._storage = storage
        self._block_id = "task_status"  # Single block for all task statuses
        
    async def _ensure_block(self):
        """Ensure the storage block exists."""
        await self._storage.create_block(self._block_id, overwrite=False)

    async def _build_data_in_store(self, data: TaskStatusData) -> Data:
        """Build Data instance for storage."""
        return Data(value=data, block_id=self._block_id)

    async def register(self, task_id: str, status: str = TaskStatus.INIT):
        """Register a new task with initial status."""
        await self._ensure_block()
        data = await self._build_data_in_store(TaskStatusData(
            task_id=task_id,
            status=status,
            reason=None,
            updated_at=time.time(),
        ))
        # Use overwrite=False to prevent overwriting existing tasks
        existing = await self._get_data(task_id)
        if not existing:
            await self._storage.create_data(data, block_id=self._block_id, overwrite=False)

    async def set_status(self, task_id: str, status: str, reason: Optional[str] = None):
        """Set task status with optional reason."""
        await self._ensure_block()
        data = await self._build_data_in_store(TaskStatusData(
            task_id=task_id,
            status=status,
            reason=reason,
            updated_at=time.time(),
        ))
        await self._storage.create_data(data, block_id=self._block_id, overwrite=True)

    async def is_finished(self, task_id: str) -> bool:
        """Check if task is finished."""
        data = await self._get_data(task_id)
        return bool(data and data.status in
                    [TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.INTERRUPTED, TaskStatus.TIMEOUT])

    async def cancel(self, task_id: str, reason: Optional[str] = None):
        """Cancel a task."""
        await self.set_status(task_id, TaskStatus.CANCELLED, reason)

    async def is_cancelled(self, task_id: str) -> bool:
        """Check if task is cancelled."""
        data = await self._get_data(task_id)
        return bool(data and data.status == TaskStatus.CANCELLED)

    async def interrupt(self, task_id: str, reason: Optional[str] = None):
        """Interrupt a task."""
        await self.set_status(task_id, TaskStatus.INTERRUPTED, reason)

    async def is_interrupted(self, task_id: str) -> bool:
        """Check if task is interrupted."""
        data = await self._get_data(task_id)
        return bool(data and data.status == TaskStatus.INTERRUPTED)

    async def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task status information."""
        data = await self._get_data(task_id)
        if not data:
            return None
        return {
            "status": data.status,
            "reason": data.reason,
            "updated_at": data.updated_at,
        }

    async def _get_data(self, task_id: str) -> Optional[TaskStatusData]:
        """Internal method to get task status data."""
        condition: Condition = {
            "field": "task_id",
            "value": task_id,
            "op": "eq"
        }
        results = await self._storage.select_data(condition)
        if results:
            return results[0].value
        return None


class InMemoryTaskStatusStore(TaskStatusStore):
    """In-memory task status store using InmemoryStorage backend."""
    
    def __init__(self):
        """Initialize with InmemoryStorage backend."""
        from aworld.core.storage.inmemory_store import InmemoryStorage, InmemoryConfig
        storage = InmemoryStorage(InmemoryConfig())
        super().__init__(storage)


class RedisTaskStatusStore(TaskStatusStore):
    """Redis-based task status store using RedisStorage backend."""
    
    def __init__(self, *, host: str = "127.0.0.1", port: int = 6379, db: int = 0,
                 password: Optional[str] = None, prefix: str = "aworld:task_status:"):
        """Initialize with RedisStorage backend.
        
        Args:
            host: Redis host address
            port: Redis port
            db: Redis database number
            password: Redis password (optional)
            prefix: Key prefix for Redis keys
        """
        from aworld.core.storage.redis_store import RedisStorage, RedisConfig
        
        config = RedisConfig(
            name="task_status",
            host=host,
            port=port,
            db=db,
            password=password,
            key_prefix=prefix,
            index_name=f"idx:{prefix}",
            data_schema={
                "task_id": str,
                "status": str,
                "reason": str,
                "updated_at": float
            }
        )
        storage = RedisStorage(config)
        super().__init__(storage)


class SQLiteTaskStatusStore(TaskStatusStore):
    """SQLite-based task status store.
    
    Note: SQLite storage is not yet implemented in the Storage abstraction.
    This is a placeholder for future implementation.
    """
    
    def __init__(self, file: str = "/tmp/aworld_task_status.db"):
        """Initialize with SQLite storage (not yet implemented).
        
        Args:
            file: Path to SQLite database file
            
        Raises:
            NotImplementedError: SQLite Storage backend is not yet implemented
        """
        # TODO: Implement SQLite Storage backend
        raise NotImplementedError(
            "SQLiteTaskStatusStore based on Storage abstraction is not yet implemented. "
            "Please use InMemoryTaskStatusStore or RedisTaskStatusStore."
        )


class TaskStatusRegistry(InheritanceSingleton):
    """Task status registry.

    Exposes a unified API with pluggable storage backends.
    All methods are async to support the Storage abstraction.
    """

    def __init__(self, store: TaskStatusStore = None):
        self._store: TaskStatusStore = store or InMemoryTaskStatusStore()

    def use_store(self, store: TaskStatusStore):
        """Switch to a different storage backend.
        
        Args:
            store: TaskStatusStore instance to use
        """
        self._store = store or InMemoryTaskStatusStore()

    # Async proxy methods
    async def register(self, task_id: str, status: str = TaskStatus.INIT):
        """Register a new task with initial status."""
        await self._store.register(task_id, status)

    async def set_status(self, task_id: str, status: str, reason: Optional[str] = None):
        """Set task status with optional reason."""
        await self._store.set_status(task_id, status, reason)

    async def cancel(self, task_id: str, reason: Optional[str] = None):
        """Cancel a task."""
        await self._store.cancel(task_id, reason)

    async def is_cancelled(self, task_id: str) -> bool:
        """Check if task is cancelled."""
        return await self._store.is_cancelled(task_id)

    async def interrupt(self, task_id: str, reason: Optional[str] = None):
        """Interrupt a task."""
        await self._store.interrupt(task_id, reason)

    async def is_interrupted(self, task_id: str) -> bool:
        """Check if task is interrupted."""
        return await self._store.is_interrupted(task_id)

    async def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task status information."""
        return await self._store.get(task_id)


def build_task_status_store(conf: Optional[Dict[str, Any]]) -> TaskStatusStore:
    """Build a TaskStatusStore from configuration.
    
    The store now uses the Storage abstraction layer, supporting pluggable backends.

    Example config:
    {
        "backend": "memory",  # memory | redis | sqlite
        "redis": {
            "host": "127.0.0.1", 
            "port": 6379, 
            "db": 0, 
            "password": null, 
            "prefix": "aworld:task_status:"
        },
        "sqlite": {
            "file": "/tmp/aworld_task_status.db"
        }
    }
    
    Returns:
        TaskStatusStore: Configured task status store instance
    """
    conf = conf or {}
    backend = conf.get("backend", "memory").lower()
    
    try:
        if backend == "redis":
            params = conf.get("redis") or {}
            return RedisTaskStatusStore(**params)
        elif backend in ("sqlite", "db", "database"):
            params = conf.get("sqlite") or {}
            logger.warning("SQLite backend not yet fully implemented, falling back to memory")
            return InMemoryTaskStatusStore()
        else:
            # Default to in-memory storage
            return InMemoryTaskStatusStore()
    except Exception as e:
        logger.warning(f"Failed to build task status store, falling back to memory store. Error: {e}")
        return InMemoryTaskStatusStore()


def build_task_status_store_from_storage(storage: Storage[TaskStatusData]) -> TaskStatusStore:
    """Build a TaskStatusStore directly from a Storage instance.
    
    This is useful when you want to use a custom Storage backend or
    have more fine-grained control over the Storage configuration.
    
    Args:
        storage: Storage instance configured for TaskStatusData
        
    Returns:
        TaskStatusStore: Task status store using the provided storage
        
    Example:
        from aworld.core.storage.inmemory_store import InmemoryStorage, InmemoryConfig
        
        storage = InmemoryStorage(InmemoryConfig(max_capacity=5000))
        store = build_task_status_store_from_storage(storage)
        
        # Or you can directly instantiate:
        store = TaskStatusStore(storage)
    """
    return TaskStatusStore(storage)
