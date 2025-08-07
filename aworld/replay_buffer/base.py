import random
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, TypeVar
from abc import ABC, abstractmethod
from math import ceil

from aworld.core.common import ActionModel, Observation
from aworld.core.storage.base import Storage, InMemoryStorage
from aworld.core.storage.condition import Condition
from aworld.logs.util import logger
from aworld.utils.serialized_util import to_serializable

T = TypeVar('T')


@dataclass
class Experience:
    '''
    Experience of agent.
    '''
    state: Observation
    actions: List[ActionModel]
    reward_t: float = None
    adv_t: float = None
    v_t: float = None
    messages: List[Dict] = None

    def to_dict(self):
        return {
            "state": to_serializable(self.state),
            "actions": to_serializable(self.actions),
            "reward_t": self.reward_t,
            "adv_t": self.adv_t,
            "v_t": self.v_t,
            "messages": self.messages
        }


@dataclass
class ExpMeta:
    '''
    Experience meta data.
    '''
    task_id: str
    task_name: str
    agent_id: str
    step: int
    execute_time: float
    pre_agent: str

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "agent_id": self.agent_id,
            "step": self.step,
            "execute_time": self.execute_time,
            "pre_agent": self.pre_agent
        }
@dataclass
class DataRow:
    '''
    Data row for storing data.
    '''
    exp_meta: ExpMeta
    exp_data: Experience
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self):
        return {
            "exp_meta": self.exp_meta.to_dict(),
            "exp_data": self.exp_data.to_dict(),
            "id": self.id
        }


class Sampler(ABC):
    '''
    Sample data from the storage.
    '''

    def sample(self,
               storage: Storage,
               batch_size: int,
               query_condition: Condition = None) -> List[DataRow]:
        '''
        Sample data from the storage.
        Args:
            storage (Storage): Storage to sample from.
            batch_size (int): Number of data to sample.
            query_condition (QueryCondition, optional): Query condition. Defaults to None.
        Returns:
            List[DataRow]
        '''


class TaskSampler(Sampler):
    '''
    Sample task data from storage, returns Dict[str, List[DataRow]] where:
    - key is task_id
    - value is list of task all data rows
    '''

    def sorted_by_step(self, task_experience: List[DataRow]) -> List[DataRow]:
        '''
        Sort the task experience by step and execute_time.
        Args:
            task_experience (List[DataRow]): List of task experience.
        Returns:
            List[DataRow]: List of task experience sorted by step and execute_time.
        '''
        return sorted(task_experience, key=lambda x: (x.exp_meta.step, x.exp_meta.execute_time))

    def sample(self,
               storage: Storage,
               batch_size: int,
               query_condition: Condition = None) -> List[DataRow]:
        task_ids = self.sample_task_ids(storage, batch_size, query_condition)
        return storage.get_bacth_by_task_ids(task_ids)

    def sample_tasks(self,
                     storage: Storage,
                     batch_size: int,
                     query_condition: Condition = None) -> Dict[str, List[DataRow]]:
        '''
        Sample data from the storage.
        Args:
            storage (Storage): Storage to sample from.
            batch_size (int): Number of data to sample.
            query_condition (QueryCondition, optional): Query condition. Defaults to None.
        Returns:
            Dict[str, List[DataRow]]: Dictionary of sampled data.
            The key is the task_id and the value is the list of data.
            The list of data is sorted by step.
        '''
        task_ids = self.sample_task_ids(storage, batch_size, query_condition)
        raws = storage.get_bacth_by_task_ids(task_ids)
        return {task_id: self.sorted_by_step(raws) for task_id, raws in raws.items()}

    @abstractmethod
    def sample_task_ids(self,
                        storage: Storage,
                        batch_size: int,
                        query_condition: Condition = None) -> List[str]:
        '''
        Sample task_ids from the storage.
        Args:
            storage (Storage): Storage to sample from.
            batch_size (int): Number of task_ids to sample.
            query_condition (QueryCondition, optional): Query condition. Defaults to None.
        Returns:
            List[str]: List of task_ids.
        '''


class Converter(ABC):
    '''
    Convert data to dataset row.
    '''

    @abstractmethod
    def to_dataset_row(self, task_experience: List[DataRow]) -> T:
        '''
        Convert task experience to dataset row.
        Args:
            task_experience (List[DataRow]): List of task experience.
        Returns:
            T: type of dataset row.
        '''


class RandomTaskSample(TaskSampler):
    '''
    Randomly sample data from the storage.
    '''

    def sample_task_ids(self,
                        storage: Storage,
                        batch_size: int,
                        query_condition: Condition = None) -> List[str]:
        total_size = storage.size(query_condition)
        if total_size <= batch_size:
            return storage.get_all(query_condition)

        sampled_task_ids = set()
        page_size = min(100, batch_size * 2)
        total_pages = ceil(total_size/page_size)
        visited_pages = set()
        while len(sampled_task_ids) < batch_size and len(visited_pages) < total_pages:
            page = random.choice(
                [p for p in range(1, total_pages+1) if p not in visited_pages])
            visited_pages.add(page)

            current_page = storage.get_paginated(
                page, page_size, query_condition)
            if not current_page:
                continue
            current_page_task_ids = set(
                [data.exp_meta.task_id for data in current_page if data.exp_meta.task_id not in sampled_task_ids])
            sample_count = min(len(current_page_task_ids),
                               batch_size - len(sampled_task_ids))
            sampled_task_ids.update(random.sample(
                list(current_page_task_ids), sample_count))

        return list(sampled_task_ids)


class DefaultConverter(Converter):
    '''
    Default converter do nothing.
    '''

    def to_dataset_row(self, task_experience: List[DataRow]) -> List[DataRow]:
        return task_experience


class ReplayBuffer:
    '''
    Replay buffer for storing and sampling data.
    '''

    def __init__(
        self,
        storage: Storage = InMemoryStorage()
    ):
        self._storage = storage

    def store(self, data: DataRow):
        '''
        Store data in the replay buffer.
        '''
        if not data:
            raise ValueError("Data is required")
        self._storage.add(data)

    def store_batch(self, data_batch: List[DataRow]):
        '''
        Store batch of data in the replay buffer.
        '''
        if not data_batch:
            raise ValueError("Data batch is required")
        self._storage.add_batch(data_batch)

    def sample_task(self,
                    sampler: TaskSampler = RandomTaskSample(),
                    query_condition: Condition = None,
                    converter: Converter = DefaultConverter(),
                    batch_size: int = 1000) -> List[T]:
        '''
        Sample Task from the replay buffer and convert to dataset row.
        DefaultConverter return List[DataRow]
        '''
        sampled_task = sampler.sample_tasks(
            self._storage, batch_size, query_condition)
        return [converter.to_dataset_row(task_experiences) for task_experiences in sampled_task.values()]

    def sample(self,
               sampler: Sampler = RandomTaskSample(),
               query_condition: Condition = None,
               converter: Converter = DefaultConverter(),
               batch_size: int = 1000) -> List[T]:
        '''
        Sample data from the replay buffer and convert to dataset row.
        DefaultConverter return List[DataRow]
        '''
        sampled_data = sampler.sample(
            self._storage, batch_size, query_condition)
        return converter.to_dataset_row(sampled_data)
