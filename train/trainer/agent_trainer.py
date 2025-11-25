# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from typing import Callable, Union, Type, Dict

from datasets import Dataset

from aworld.agents.llm_agent import Agent
from aworld.config import TaskConfig
from aworld.core.common import Config
from aworld.core.context.amni import AmniContextConfig
from aworld.logs.util import logger
from train.adapter.verl.verl_trainer import VerlTrainer
from train.trainer.trainer_processor import TrainerProcessor

TRAIN_PROCESSOR: Dict[str, Type[TrainerProcessor]] = {
    'verl': VerlTrainer,
}


class AgentTrainer:
    """Aworld's agent training unified API entrance, supporting different train frameworks behind the trainer."""

    def __init__(self,
                 # Unsupported swarm now
                 agent: Union[str, Agent],
                 config: Union[str, Config] = None,
                 reward_func: Union[str, Callable[..., float]] = None,
                 train_dataset: Union[str, Dataset] = None,
                 test_dataset: Union[str, Dataset] = None,
                 context_config: AmniContextConfig = None,
                 task_config: TaskConfig = None,
                 run_path: str = None,
                 train_engine_name: str = 'verl') -> None:
        """AgentTrainer initialization, 4 modules are required (agent, dataset, reward, config).

        Args:
            agent: Agent module, AWorld agent, or agent config file path that can build the AWorld agent.
            reward_func: Reward module, reward function or reward function code file path.
                         Can set in `config`, so it can be None.
            train_dataset: Dataset module, train dataset or dataset file path that can build the training dataset.
                           Can set in `config`, so it can be None.
            test_dataset: Dataset module, test dataset or dataset file path that can build the test dataset.
                          Can set in `config`, so it can be None.
            config: Train config module, custom training configuration of special train framework.
                    Some frameworks may already have default configs, so it can be None.
            run_path: The path to save the running code, logs and checkpoints, default is 'workspace/runs'.
            train_engine_name: The training framework to use, default is 'verl'.
        """

        assert agent, "trainer agent is required!"

        self.agent = agent
        self.train_dataset = train_dataset
        self.test_dataset = test_dataset
        self.reward_func = reward_func
        self.config = config
        self.train_engine_name = train_engine_name

        if run_path is None:
            self.run_path = os.path.join(os.getcwd(), 'runs')
        else:
            self.run_path = run_path
        os.makedirs(self.run_path, exist_ok=True)

        engine_cls = TRAIN_PROCESSOR.get(train_engine_name)
        if not engine_cls:
            raise ValueError(f"{train_engine_name} is not supported")

        train_engine = engine_cls(self.run_path)
        if not isinstance(train_engine, TrainerProcessor):
            raise ValueError(f"{train_engine_name} train engine is not a TrainerProcessor")

        # process prerequisite modules
        agent_config = train_engine.check_agent(agent=agent, context_config=context_config, task_config=task_config)
        train_engine.check_dataset(dataset=train_dataset, test_dataset=test_dataset)
        train_engine.check_reward(reward_func=reward_func)
        real_config = train_engine.check_config(config=config)
        train_engine.mark_initialized()

        logger.info(f"Agent Config: {agent_config} \n Train config: {real_config}")
        self.train_processor = train_engine

    @staticmethod
    def register_processor(train_engine_name: str, train_type: Type[TrainerProcessor]):
        """Register a train engine processor for agent training."""
        if train_engine_name in TRAIN_PROCESSOR:
            raise ValueError(f"{train_engine_name} is already registered")

        TRAIN_PROCESSOR[train_engine_name] = train_type

    @staticmethod
    def unregister_processor(train_engine_name: str):
        TRAIN_PROCESSOR.pop(train_engine_name, None)

    def train(self):
        if self.train_processor.initialized:
            self.train_processor.train()
        else:
            raise ValueError(f"Train engine {self.train_engine_name} is not initialized")
