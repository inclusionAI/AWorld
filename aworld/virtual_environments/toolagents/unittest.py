# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import argparse
import os

from aworld.agents.gaia.agent import ExecuteAgent, PlanAgent
from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.client import Client
from aworld.core.common import Tools
from aworld.core.swarm import Swarm
from aworld.core.task import Task
from aworld.logs.util import logger

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # parser.add_argument("--task_description", type=str, default="What animals are in the given image and what text content is included in the image?")
    parser.add_argument("--task_description", type=str, default="")
    parser.add_argument("--file_path", type=str, required=False, default="")
    # parser.add_argument("--file_path", type=str, default="")
    args = parser.parse_args()
    task_description = args.task_description
    file_path = args.file_path
    # if not file_path:
    #     raise ValueError("Please provide a file path")
    if file_path and not os.path.exists(file_path):
        raise ValueError("File path does not exist")

    # Initialize client
    client = Client()

    # One sample for example
    test_sample = f"{task_description}\nThe relevant file path is {file_path}"

    # os.environ["LLM_API_KEY"] = "sk-z"
    # os.environ["LLM_BASE_URL"] = "https://api."

    llm_api_key = os.getenv("LLM_API_KEY", "")
    llm_base_url = os.getenv("LLM_BASE_URL", "")
    # Create agents
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_temperature=0.0,
    )

    planner = PlanAgent(conf=agent_config)
    executor = ExecuteAgent(
        conf=agent_config,
        tool_names=[
            # Tools.AUDIO_ANALYSIS.value,
            # Tools.IMAGE_ANALYSIS.value,
            # Tools.VIDEO_ANALYSIS.value,
        ],
        mcp_servers=[
            "image",
            "audio",
            "video",
            # "amap-amap-sse",
        ],
    )

    swarm = Swarm((planner, executor))

    # Define a task
    task = Task(input=test_sample, swarm=swarm, conf=TaskConfig())

    # Run task
    result = client.submit(task=[task])

    logger.success(f"Task completed: {result['success']}")
    logger.success(f"Time cost: {result['time_cost']}")
    logger.success(f"Task Answer: {result['task_0']['answer']}")
