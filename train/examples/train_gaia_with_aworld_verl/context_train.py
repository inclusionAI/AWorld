# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import os

from dotenv import load_dotenv

from aworld.config import TaskConfig
from train.examples.train_gaia_with_aworld_verl.mcp_tools import build_mcp_config
from train.examples.train_gaia_with_aworld_verl.reward import gaia_reward_func
from train.examples.train_gaia_with_aworld_verl.rollout import build_context_aware_agent
from train.examples.train_gaia_with_aworld_verl.rollout.agent_config import build_context_aware_task_config


async def main():
    # config module divided into environmental variables and training configurations
    success = load_dotenv()
    custom_train_config = 'train/examples/train_gaia_with_aworld_verl/grpo_trainer.yaml'

    # # agent module contains agent and mcp tools in environment
    # mcp_config = {
    #     "mcpServers": {
    #         "gaia_server": {
    #             "type": "streamable-http",
    #             "url": "https://playground.aworldagents.com/environments/mcp",
    #             "timeout": 600,
    #             "sse_read_timeout": 600,
    #             "headers": {
    #                 "ENV_CODE": "gaia",
    #                 "Authorization": f'Bearer {os.environ.get("INVITATION_CODE", "")}',
    #             }
    #         }
    #     }
    # }
    # agent_config = AgentConfig(
    #     llm_provider="verl",
    #     top_k=80
    # )
    # agent = Agent(
    #     name="gaia_agent",
    #     desc="gaia_agent",
    #     system_prompt="Gaia agent",
    #     mcp_config=mcp_config,
    #     mcp_servers=["gaia_server"],
    #     conf=agent_config
    # )
    from train.trainer.agent_trainer import AgentTrainer
    from aworld.dataset.trajectory_strategy import MemoryTrajectoryStrategy
    agent = build_context_aware_agent(llm_model_name=os.getenv("LLM_MODEL_NAME"),
                             llm_base_url=os.getenv("LLM_BASE_URL"),
                             llm_api_key=os.getenv("LLM_API_KEY"),
                             mcp_config=await build_mcp_config())
    context_config = build_context_aware_task_config()
    task_config = TaskConfig(
        stream=False,
        exit_on_failure=True,
        trajectory_strategy=MemoryTrajectoryStrategy
    )

    # dataset module contains train and test dataset
    train_dataset = f'train/examples/train_gaia_with_aworld_verl/gaia_data/sample_train.parquet'
    test_dataset = f'train/examples/train_gaia_with_aworld_verl/gaia_data/sample_test.parquet'
    abs_train_dataset = os.path.abspath(train_dataset)
    abs_test_dataset = os.path.abspath(test_dataset)

    # reward module contains reward function or reward function code file path
    reward_func = gaia_reward_func

    trainer = AgentTrainer(agent=agent,
                           context_config=context_config,
                           task_config=task_config,
                           config=custom_train_config,
                           reward_func=reward_func,
                           train_dataset=abs_train_dataset,
                           test_dataset=abs_test_dataset)
    trainer.train()


if __name__ == "__main__":
    asyncio.run(main())
