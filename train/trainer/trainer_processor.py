# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from typing import Union, Callable, Any

from datasets import Dataset

from aworld.agents.llm_agent import Agent


class TrainerProcessor:
    """Trainer engine processor base class.

    The API definition for training in AWorld is 5 module: Agent， Data, Rewards, Training, and their configuration.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, run_path: str):
        self.run_path = run_path
        self._initialized = False

    @abc.abstractmethod
    def train(self):
        """Training process implementation of the specified training backend.

        Before calling, it is necessary to correctly process the required dataset, agent, reward, and configuration.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def check_dataset(self, dataset: Union[str, Dataset], test_dataset: Union[str, Dataset] = None):
        """Check if the dataset or configuration meets the requirements of the specified training backend.

        Args:
            dataset: The dataset to be checked.
            test_dataset: The test dataset to be checked.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def check_agent(self, agent: Union[str, Agent]):
        """Check AWorld's agent or configuration, and process it into a specific training backend supported form.

        Args:
            agent: The agent or configure to be the agent you want to train.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def check_reward(self, reward_func: Union[str, Callable[..., Any]]):
        """Check if the reward function or configuration meets the requirements of the specified training backend.

        Args:
            reward_func: The reward callable function or configuration to be checked.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def check_config(self, config: Union[str, Any]):
        """Check if the train configuration meets the requirements of the specified training backend, and improve it.

        Args:
            config: The training configuration to be checked.
        """
        raise NotImplementedError

    @property
    def initialized(self) -> bool:
        return self._initialized

    @initialized.setter
    def initialized(self, value: bool):
        self._initialized = value

    def mark_initialized(self):
        self._initialized = True
