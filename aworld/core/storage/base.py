# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from abc import abstractmethod, ABCMeta
from typing import Generic, TypeVar, List

from aworld.config import StorageConfig
from aworld.core.storage.condition import Condition
from aworld.core.storage.data import Data, DataBlock
from aworld.logs.util import logger

DataItem = TypeVar('DataItem', bound=Data)


class Storage(Generic[DataItem]):
    """Storage with client."""
    __metaclass__ = ABCMeta

    def __init__(self, conf: StorageConfig):
        self.conf = conf

    @abstractmethod
    def backend(self):
        """Storage backend instance, used to perform native operations on storage."""

    @abstractmethod
    async def delete_all(self):
        """Close the storage backend."""

    async def name(self) -> str:
        """The name of storage."""
        return self.conf.name

    @abstractmethod
    async def create_block(self, block_id: str, overwrite: bool = True) -> bool:
        """"""

    @abstractmethod
    async def delete_block(self, block_id: str, exists: bool = False) -> bool:
        """"""

    @abstractmethod
    async def get_block(self, block_id: str) -> DataBlock:
        """"""

    @abstractmethod
    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        """Add data to the block of the storage.

        Args:
            data: Data item or data item list to add the storage.
            block_id: The block id of data, analogous to a dir for storing files.
            overwrite: Can the same data be overwritten, True is yes.
        """

    async def create_datas(self, data: List[DataItem], block_id: str = None, overwrite: bool = True) -> bool:
        res = True
        if not data:
            logger.warning("no data to store.")
            return res

        for d in data:
            res = res & await self.create_data(d, block_id, overwrite)
        return res

    @abstractmethod
    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        """Update data of the block of the storage.

        Args:
            data: Data item or data item list to add the storage.
            block_id: The block of data, analogous to a dir for storing datas.
            exists: Whether the data must exist, True is yes.
        """

    async def update_datas(self, data: List[DataItem], block_id: str = None, exists: bool = False) -> bool:
        res = True
        if not data:
            logger.warning("no data to update.")
            return res

        for d in data:
            res = res & await self.update_data(d, block_id, exists)
        return res

    @abstractmethod
    async def delete_data(self, data_id: str, block_id: str = None, exists: bool = False) -> bool:
        """Delete data of the block of the storage.

        Args:
            data_id: Data item id.
            block_id: The block of data, analogous to a dir for storing datas.
            exists: Whether the data must exist, True is yes.
        """

    async def delete_datas(self, data: List[str], block_id: str = None, exists: bool = False) -> bool:
        res = True
        if not data:
            logger.warning("no data to delete.")
            return res

        for d in data:
            res = res & await self.delete_data(d, block_id, exists)
        return res

    @abstractmethod
    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        """Get the data list by condition from the storage.

        Args:
            condition: Query condition.

        Returns:
            List of data.
        """

    @abstractmethod
    async def get_data(self, block_id: str = None) -> List[DataItem]:
        """Get the data list of block from the storage.

        Args:
            block_id: The block of data, analogous to a dir for storing datas.

        Returns:
            List of data.
        """

    @abstractmethod
    async def size(self, condition: Condition = None) -> int:
        """Get the size of the storage by the condition.

        Args:
            condition: Query condition.

        Returns:
            int: Size of data item in the storage.
        """
