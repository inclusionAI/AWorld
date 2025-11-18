# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from typing import Callable, Union

from datasets import Dataset
from aworld.agents.llm_agent import Agent
from aworld.core.common import Config
from aworld.logs.util import logger
from train.adapter.verl.verl_trainer import VerlTrainer

TRAIN_BACKEND = {
    'verl': VerlTrainer,
}


class AgentTrainer:
    def __init__(self,
                 # Unsupported swarm now
                 agent: Union[str, Agent],
                 config: Union[str, Config],
                 reward_func: Union[str, Callable[..., float]],
                 train_dataset: Union[str, Dataset],
                 test_dataset: Union[str, Dataset] = None,
                 run_path: str = None,
                 train_backend: str = 'verl') -> None:
        self.agent = agent
        self.train_dataset = train_dataset
        self.test_dataset = test_dataset
        self.reward_func = reward_func
        self.config = config
        self.train_backend = train_backend

        if run_path is None:
            self.run_path = os.path.join(os.getcwd(), 'runs')
        else:
            self.run_path = run_path
        os.makedirs(self.run_path, exist_ok=True)

        backend_cls = TRAIN_BACKEND.get(train_backend)
        if not backend_cls:
            raise ValueError(f"{train_backend} is not supported")

        backend = backend_cls(self.run_path)

        backend.check_agent(agent=agent)
        backend.check_dataset(train_dataset)
        backend.check_reward(reward_func=reward_func)
        self.config = backend.check_config(config=config)
        logger.info(f"Train config: {self.config}")
        backend.mark_initialized()
        self.backend = backend

    def train(self):
        self.backend.train()
