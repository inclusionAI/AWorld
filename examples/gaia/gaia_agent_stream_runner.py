import argparse
import asyncio
import json
import logging
import os
import re
import sys
import traceback
from typing import Any, Dict, List

from dotenv import load_dotenv

from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.task import Task
from aworld.output.base import *
from aworld.runner import Runners
from examples.gaia.prompt import system_prompt
from examples.gaia.utils import (
    add_file_path,
    load_dataset_meta_dict,
    question_scorer,
)

aworld_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(aworld_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


# Create log directory if it doesn't exist
if not os.path.exists(os.getenv("LOG_FILE_PATH")):
    os.makedirs(os.getenv("LOG_FILE_PATH"))


async def process_final_result(output):
    if isinstance(output, MessageOutput):
        print(output.response)
    elif isinstance(output, ToolResultOutput):
        print(output.response)
    elif isinstance(output, ToolCallOutput):
        print(output.response)


async def send_output(output):
    gaia_output_line_tag = "GA_FMT_C:"
    if isinstance(output, MessageOutput):
        print(f"{gaia_output_line_tag}{output.response}")
    elif isinstance(output, ToolResultOutput):
        print(f"{gaia_output_line_tag}{output.response}")
    elif isinstance(output, ToolCallOutput):
        print(f"{gaia_output_line_tag}{output.response}")


async def main():
    logger.info(f"Start gaia agent runner!")

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=str, default="")
    args = parser.parse_args()

    load_dotenv()

    agent_config = AgentConfig(
        llm_provider=os.getenv("LLM_PROVIDER"),
        llm_model_name=os.getenv("LLM_MODEL_NAME"),
        llm_api_key=os.getenv("LLM_API_KEY"),
        llm_base_url=os.getenv("LLM_BASE_URL"),
    )
    super_agent = Agent(
        conf=agent_config,
        name="gaia_super_agent",
        system_prompt=system_prompt,
        mcp_servers=[
            "e2b-server",
            # "filesystem",
            "terminal-controller",
            "excel",
            "calculator",
            "ms-playwright",
            "audio_server",
            "image_server",
            "video_server",
            "search_server",
            "download_server",
            "document_server",
            # "browser_server",
            "youtube_server",
            "reasoning_server",
        ],
    )

    mcp_servers = ", ".join(super_agent.mcp_servers)
    logger.info(
        f"Agent Info: name=gaia_super_agent, Environment MCP servers={mcp_servers}\n"
    )

    result = None
    try:
        prompt = args.prompt

        question = None
        try:
            json_data = json.loads(prompt)
            task_id = json_data["task_id"]
            gaia_dataset_path = os.getenv("GAIA_DATASET_PATH", "./GAIA/2023/test")
            full_dataset = load_dataset_meta_dict(gaia_dataset_path)
            data_item = full_dataset[task_id]
            question = add_file_path(
                data_item, file_path=gaia_dataset_path
            )["Question"]
            logger.info(f"Start to process: {data_item}")
        except Exception as e:
            pass

        if not question:
            logger.warning("Could not find GAIA question for prompt, chat using prompt directly!")
            question = prompt

        task = Task(input=question, agent=super_agent, conf=TaskConfig())

        last_output = None
        async for output in Runners.streamed_run_task(task).stream_events():
            await send_output(output);
            last_output = output

        if last_output:
            await process_final_result(last_output)
    except Exception as e:
        logger.error(
            f"Error processing {args.prompt}, result: {result}, error: {traceback.format_exc()}"
        )


if __name__ == "__main__":
    asyncio.run(main())
