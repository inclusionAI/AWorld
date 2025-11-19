# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import os

from datasets import load_dataset
from dotenv import load_dotenv

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig

from train.trainer.agent_trainer import AgentTrainer
from train.examples.train_gaia_with_aworld_verl.metrics.gaia_reward_function import gaia_reward_func


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
        name="gaia_agent",
        desc="gaia_agent",
        system_prompt="Gaia agent",
        mcp_config=mcp_config,
        mcp_servers=["gaia_server"],
        conf=agent_config
    )

    # dataset module contains train and test dataset
    train_dataset = f'train/examples/train_gaia_with_aworld_verl/gaia_data/sample_train.parquet'
    test_dataset = f'train/examples/train_gaia_with_aworld_verl/gaia_data/sample_test.parquet'
    abs_train_dataset = os.path.abspath(train_dataset)
    abs_test_dataset = os.path.abspath(test_dataset)


    # reward module contains reward function or reward function code file path
    reward_func = gaia_reward_func

    trainer = AgentTrainer(agent=agent,
                           config=custom_train_config,
                           reward_func=reward_func,
                           train_dataset=abs_train_dataset,
                           test_dataset=abs_test_dataset)
    trainer.train()


if __name__ == "__main__":
    main()
