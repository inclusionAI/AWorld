# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os

from loguru import logger

from aworld.agents.gaia.agent import ExecuteAgent, PlanAgent
from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.client import Client
from aworld.core.common import ActionModel, Agents, Observation, Tools
from aworld.core.swarm import Swarm
from aworld.core.task import Task
from aworld.dataset.mock import mock_dataset

if __name__ == "__main__":
    # Initialize client
    client = Client()

    # One sample for example
    filepath = "/Users/arac/Desktop/qw.jpg"
    test_sample = (
        f"What animal is in the given picture? The picture file path is {filepath}"
    )

    llm_api_key = os.getenv("LLM_API_KEY", "")
    llm_base_url = os.getenv("LLM_BASE_URL", "")
    # Create agents
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
    )

    planner = PlanAgent(conf=agent_config)
    executor = ExecuteAgent(conf=agent_config, tool_names=[Tools.IMAGE_ANALYSIS.value])

    swarm = Swarm((planner, executor))

    # Define a task
    task = Task(input=test_sample, swarm=swarm, conf=TaskConfig())

    # Run task
    result = client.submit(task=[task])

    logger.success(f"Task completed: {result['success']}")
    logger.success(f"Time cost: {result['time_cost']}")
    logger.success(f"Task Answer: {result['task_0']['answer']}")
