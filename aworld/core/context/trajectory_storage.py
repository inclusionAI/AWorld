# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from abc import abstractmethod
from typing import List, Dict, Any, Union

from aworld.core.storage.base import Storage, DataItem
from aworld.core.storage.inmemory_store import InmemoryStorage


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

