# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from abc import abstractmethod
from typing import List, Union, Optional, Type

from aworld.core.storage.base import Storage, DataItem
from aworld.core.storage.inmemory_store import InmemoryStorage
from aworld.logs.util import logger


class TrajectoryStorage(Storage[DataItem]):
    """
    Trajectory storage interface.
    """
    @abstractmethod
    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        """
        Add data to the block of the storage.
        In trajectory storage, block_id is task_id.

        Args:
            data: Data item (one step of trajectory).
            block_id: The task_id.
            overwrite: Whether to overwrite existing data.
        """

    @abstractmethod
    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        """
        Get all trajectory data items for a task.

        Args:
            block_id: The task_id.
        """


class InMemoryTrajectoryStorage(InmemoryStorage):
    """
    In-memory implementation of TrajectoryStorage.
    """
    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        """
        Add trajectory data (step) to memory.
        """
        return await super().create_data(data, block_id, overwrite)

    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        """
        Get trajectory data from memory.
        """
        return await super().get_data_items(block_id)


def get_storage_instance(storage: Optional[Union[Type[TrajectoryStorage], TrajectoryStorage, str]]) -> TrajectoryStorage:
    """
    Get a TrajectoryStorage instance from various input types.
    
    Args:
        storage: Can be a class, instance, string (module path), or None
        
    Returns:
        TrajectoryStorage instance
    """
    if storage is None:
        return InMemoryTrajectoryStorage()
    
    # Already an instance
    if isinstance(storage, TrajectoryStorage):
        return storage
    
    # String path to class
    if isinstance(storage, str):
        from aworld.utils.common import new_instance
        try:
            return new_instance(storage)
        except Exception as e:
            logger.warning(f"Failed to instantiate storage from string {storage}: {e}, using default")
            return InMemoryTrajectoryStorage()
    
    # Class type
    if isinstance(storage, type):
        try:
            if issubclass(storage, TrajectoryStorage):
                return storage()
        except (TypeError, ValueError):
            pass
        logger.warning(f"Storage class {storage} is not a valid TrajectoryStorage subclass, using default")
        return InMemoryTrajectoryStorage()
    
    # Unknown type
    logger.warning(f"Storage has unexpected type {type(storage)}, using default")
    return InMemoryTrajectoryStorage()

