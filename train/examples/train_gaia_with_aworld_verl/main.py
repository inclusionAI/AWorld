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
    success = load_dotenv()
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
    import inspect
    code = inspect.getfile(gaia_reward_func)
    print(code)

    train_dataset = load_dataset("", split="train")
    test_dataset = load_dataset("", split="test")

    trainer = AgentTrainer(agent=agent,
                           config='examples/ppo_trainer.yaml',
                           reward_func=gaia_reward_func,
                           train_dataset=train_dataset,
                           test_dataset=test_dataset)
    trainer.train()


if __name__ == "__main__":
    main()
