# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from typing import Union, Callable, Any

from datasets import Dataset

from aworld.agents.llm_agent import Agent


class TrainWrapper:
    """Trainer Wrapper Base Class.
    The API definition for training in AWorld is 5 blocks, agent， Data, rewards, training, and their configuration.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, run_path: str):
        self.run_path = run_path
        self._initialized = False

    @abc.abstractmethod
    def train(self):
        pass

    @abc.abstractmethod
    def check_reward(self, reward_func: Union[str, Callable[..., Any]]):
        pass

    @abc.abstractmethod
    def check_dataset(self, dataset: Union[str, Dataset]):
        pass

    @abc.abstractmethod
    def check_agent(self, agent: Agent):
        pass

    @abc.abstractmethod
    def check_config(self, config: Union[str, Any]):
        pass

    @property
    def initialized(self) -> bool:
        return self._initialized

    @initialized.setter
    def initialized(self, value: bool):
        self._initialized = value

    def mark_initialized(self):
        self._initialized = True
