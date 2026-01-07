# coding: utf-8
# Copyright (c) inclusionAI.

import os
from typing import Union, Any, Callable, List, Dict

import yaml
from datasets import Dataset, load_dataset

from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
)
from transformers.trainer_utils import PredictionOutput
from trl import GRPOConfig, GRPOTrainer
from aworld.agents.llm_agent import Agent
from aworld.config.agent_loader import load_agents_from_yaml
from aworld.config.conf import load_config
from aworld.logs.util import logger
from train.integration.trl.composite_reward import CompositeReward, build_reward_model_fn
from train.trainer.trainer_processor import TrainerProcessor


class TrlTrainer(TrainerProcessor):
    """Local train."""

    def train(self):
        model_name = self.model_name
        model = AutoModelForCausalLM.from_pretrained(
            model_name, device_map="auto",
        )
        tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        trainer = GRPOTrainer(
            model=model,
            processing_class=tokenizer,
            args=self.config,
            train_dataset=self.train_dataset,
            eval_dataset=self.test_dataset,
            reward_funcs=self.reward_func,
        )

        trainer.train()
        trainer.save_model(self.config.output_dir)
        tokenizer.save_pretrained(self.config.output_dir)

    def inference(self, dataset: Union[str, Dataset] = None) -> PredictionOutput:
        model_name = self.model_name

        if self.config.output_dir and os.path.exists(os.path.join(self.config.output_dir, "model.safetensors")):
            model_name = self.config.output_dir
            logger.info(f"Using trained model from {model_name}")

        model = AutoModelForCausalLM.from_pretrained(
            model_name, device_map="auto",
        )
        tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True, padding_side="left")
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        trainer = GRPOTrainer(
            model=model,
            processing_class=tokenizer,
            args=self.config,
            train_dataset=self.train_dataset,
            eval_dataset=self.test_dataset,
            reward_funcs=self.reward_func,
        )

        if isinstance(dataset, str):
            dataset = load_dataset("json", data_files=dataset, split='train')

        dataset = dataset or self.test_dataset
        output = trainer.predict(test_dataset=dataset)
        return output

    def check_dataset(self, dataset: Union[str, Dataset] = None, test_dataset: Union[str, Dataset] = None):
        if isinstance(dataset, str):
            dataset = load_dataset("json", data_files=dataset, split='train')

        if isinstance(test_dataset, str):
            test_dataset = load_dataset("json", data_files=test_dataset, split='train')

        self.train_dataset = dataset
        self.test_dataset = test_dataset

    def check_config(self, config: Union[str, Any] = None):
        if isinstance(config, str):
            config = load_config(config, dir_name=".")
        elif isinstance(config, dict):
            pass
        else:
            raise RuntimeError(f"Unknown type of config: {type(config)}")

        self.model_name = config.pop('model')
        if self.reward_func_based_on_config:
            reward_model_name = config.pop("reward_model", None)
            self.check_reward(reward_model_name)

        grpo_cfg = GRPOConfig(**config)
        self.config = grpo_cfg

        # for check
        yaml.safe_dump(grpo_cfg.to_dict(), open(f"{self.run_path}/final_train_config.yaml", "w"))
        logger.info(f"View TRL final tain config in file: {self.run_path}/final_train_config.yaml")
        return grpo_cfg

    def check_agent(self, agent: Union[str, Agent]):
        if agent == 'virtual':
            return

        if isinstance(agent, str):
            # means an agent yaml config file path
            agents = load_agents_from_yaml(agent)
            if not agents:
                logger.warning("No agent found in the yaml file.")
                return
            agent = list(agents.values())[0]

        self.agent = agent

    def check_reward(self, reward_func: Union[str, Callable[..., Any], List[Callable[..., Any]]] = None):
        if isinstance(reward_func, Callable):
            reward = CompositeReward(
                reward_fns=[reward_func],
                weights=[1.],
            )
        elif isinstance(reward_func, list):
            reward = CompositeReward(
                reward_fns=reward_func,
                weights=[1. / len(reward_func)] * len(reward_func),
            )
        elif isinstance(reward_func, str):
            # build based config
            rm_fn = build_reward_model_fn(reward_func)
            reward = CompositeReward(
                reward_fns=[rm_fn],
                weights=[1.],
            )
        else:
            logger.warning("No reward function found, will load later.")
            self.reward_func_based_on_config = True
            return

        # GRPO expects either `reward_func` or `reward_funcs` depending on version
        def reward_wrapper(completions: List[str], prompts: List[str], **kwargs) -> List[float]:
            return reward(completions, prompts, **kwargs)

        self.reward_func = reward_wrapper
