# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from abc import abstractmethod, ABCMeta
from typing import Generic, TypeVar, List, Dict, Union

from aworld.core.storage.condition import Condition
from aworld.replay_buffer.query_filter import QueryFilter

DataItem = TypeVar('DataItem')


class Storage(Generic[DataItem]):
    """Storage client."""
    __metaclass__ = ABCMeta

    @abstractmethod
    def create_data(self, data: Union[DataItem, List[DataItem]], bucket: str = None) -> bool:
        """Add data to the bucket of the storage.

        Args:
            data: Data item or data item list to add the storage.
            bucket: The bucket of data, analogous to a dir for storing files.
        """

    @abstractmethod
    def update_data(self, data: Union[DataItem, List[DataItem]], bucket: str = None, overwrite: bool = False) -> bool:
        """Update data of the bucket of the storage.

        Args:
            data: Data item or data item list to add the storage.
            bucket: The bucket of data, analogous to a dir for storing files.
            overwrite: Can the same data be overwritten, True is yes.

        """

    @abstractmethod
    def delete_data(self, data: Union[DataItem, List[DataItem]], bucket: str = None) -> bool:
        """Delete data of the bucket of the storage.

        Args:
            data: Data item or data item list to add the storage.
            bucket: The bucket of data, analogous to a dir for storing files.

        """

    @abstractmethod
    def size(self, condition: Condition = None) -> int:
        """Get the size of the storage by the condition.

        Returns:
            int: Size of the storage.
        """

    @abstractmethod
    def get_paginated(self, page: int, page_size: int, condition: Condition = None) -> List[DataItem]:
        '''
        Get paginated data from the storage.
        Args:
            page (int): Page number.
            page_size (int): Number of data per page.
        Returns:
            List[DataItem]: List of data.
        '''

    @abstractmethod
    def get_all(self, condition: Condition = None) -> List[DataItem]:
        '''
        Get all data from the storage.
        Returns:
            List[DataItem]: List of data.
        '''

    @abstractmethod
    def get_by_task_id(self, task_id: str) -> List[DataItem]:
        '''
        Get data by task_id from the storage.
        Args:
            task_id (str): Task id.
        Returns:
            List[DataItem]: List of data.
        '''

    @abstractmethod
    def get_bacth_by_task_ids(self, task_ids: List[str]) -> Dict[str, List[DataItem]]:
        '''
        Get batch of data by task_ids from the storage.
        Args:
            task_ids (List[str]): List of task ids.
        Returns:
            Dict[str, List[DataItem]]: Dictionary of data.
            The key is the task_id and the value is the list of data.
            The list of data is sorted by step.
        '''


class InMemoryStorage(Storage[DataItem]):
    '''
    In-memory storage for storing and sampling data.
    '''

    def __init__(self, max_capacity: int = 10000):
        self._data: Dict[str, List[DataItem]] = {}
        self._max_capacity = max_capacity
        self._fifo_queue = []  # (task_id)

    def add(self, data: DataItem):
        if not data:
            raise ValueError("DataItem is required")
        if not data.exp_meta:
            raise ValueError("exp_meta is required")

        while self.size() >= self._max_capacity and self._fifo_queue:
            oldest_task_id = self._fifo_queue.pop(0)
            if oldest_task_id in self._data:
                del self._data[oldest_task_id]

        if data.exp_meta.task_id not in self._data:
            self._data[data.exp_meta.task_id] = []
        self._data[data.exp_meta.task_id].append(data)
        self._fifo_queue.append(data.exp_meta.task_id)

        if data.exp_meta.task_id not in self._data:
            self._data[data.exp_meta.task_id] = []
        self._data[data.exp_meta.task_id].append(data)

    def add_batch(self, data_batch: List[DataItem]):
        for data in data_batch:
            self.add(data)

    def size(self, query_condition: Condition = None) -> int:
        return len(self.get_all(query_condition))

    def get_paginated(self, page: int, page_size: int, query_condition: Condition = None) -> List[DataItem]:
        if page < 1:
            raise ValueError("Page must be greater than 0")
        if page_size < 1:
            raise ValueError("Page size must be greater than 0")
        all_data = self.get_all(query_condition)
        start_index = (page - 1) * page_size
        end_index = start_index + page_size
        return all_data[start_index:end_index]

    def get_all(self, query_condition: Condition = None) -> List[DataItem]:
        all_data = []
        query_filter = None
        if query_condition:
            query_filter = QueryFilter(query_condition)
        for data in self._data.values():
            if query_filter:
                all_data.extend(query_filter.filter(data))
            else:
                all_data.extend(data)
        return all_data

    def get_by_task_id(self, task_id: str) -> List[DataItem]:
        return self._data.get(task_id, [])

    def get_bacth_by_task_ids(self, task_ids: List[str]) -> Dict[str, List[DataItem]]:
        return {task_id: self._data.get(task_id, []) for task_id in task_ids}

    def clear(self):
        self._data = {}
        self._fifo_queue = []
