# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import os

from datasets import load_dataset
from dotenv import load_dotenv

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from train.examples.train_gaia_with_aworld_verl.reward import gaia_reward_func

from train.trainer.agent_trainer import AgentTrainer


def main():
    # config module divided into environmental variables and training configurations
    success = load_dotenv()
    custom_train_config = 'train/examples/train_gaia_with_aworld_verl/grpo_trainer.yaml'

    # agent module contains agent and mcp tools in environment
    mcp_config = {
        "mcpServers": {
            "gaia_server": {
                "type": "streamable-http",
                "url": "https://playground.aworldagents.com/environments/mcp",
                "timeout": 600,
                "sse_read_timeout": 600,
                "headers": {
                    "ENV_CODE": "gaia",
                    "Authorization": f'Bearer {os.environ.get("INVITATION_CODE", "")}',
                }
            }
        }
    }
    agent_config = AgentConfig(
        llm_provider="verl",
        top_k=80
    )
    agent = Agent(
        name="demo_agent",
        desc="demo_agent",
        system_prompt="Demo agent",
        mcp_config=mcp_config,
        mcp_servers=["gaia_server"],
        conf=agent_config
    )

    # dataset module contains train and test dataset
    # train_dataset = load_dataset("", split="train")
    # test_dataset = load_dataset("", split="test")
    train_dataset = '/AWorld/verl/data/simple_dataset.parquet'
    test_dataset = '/AWorld/verl/data/simple_dataset.parquet'

    # reward module contains reward function or reward function code file path
    reward_func = gaia_reward_func

    trainer = AgentTrainer(agent=agent,
                           config=custom_train_config,
                           reward_func=reward_func,
                           train_dataset=train_dataset,
                           test_dataset=test_dataset)
    trainer.train()


if __name__ == "__main__":
    main()
