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
from aworld.output.ui.base import AworldUI
from aworld.runner import Runners
from examples.gaia.prompt import system_prompt
from examples.gaia.utils import (
    add_file_path,
    load_dataset_meta_dict,
    question_scorer,
)
from examples.gaia.markdown_aworld_ui import MarkdownAworldUI

aworld_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(aworld_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

def judge_answer(data_item: Dict, result: Output):
    answer = result
    match = re.search(r"<answer>(.*?)</answer>", answer)
    if match:
        answer = match.group(1)
        logger.info(f"Agent answer: {answer}")
        logger.info(f"Correct answer: {data_item['Final answer']}")

        if question_scorer(answer, data_item["Final answer"]):
            logger.info(f"Question {data_item['task_id']} Correct!")
        else:
            logger.info(f"Question {data_item['task_id']} Incorrect!")

        # Create the new result record
        new_result = {
            "task_id": data_item["task_id"],
            "level": data_item["Level"],
            "question": data_item["Question"],
            "answer": data_item["Final answer"],
            "response": answer,
            "is_correct": question_scorer(answer, data_item["Final answer"]),
        }
    else:
        new_result = answer

    logger.info(f"## Final Result:\n \n```\n{json.dumps(new_result, indent=2)}\n```")

def send_output(output):
    line_output_prefix = "$$$GAIA_FMT_OUTPUT$$$"
    output = json.dumps(output, indent=None)
    print(f"\n{line_output_prefix} {output}\n")
    output_dir = os.path.join( os.path.dirname(os.path.abspath(__file__)), "output" )
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    with open(os.path.join(output_dir, "output.md"), "a") as f:
        f.write(f"{output}\n")

async def main():
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

    mcp_server_status = "\n```mcp_server_status\n"
    for i in super_agent.mcp_servers:
        mcp_server_status += f"- {i}\n"
    mcp_server_status += "```\n"

    send_output(f"{mcp_server_status}")

    result = None
    try:
        prompt = args.prompt

        question = None
        try:
            json_data = json.loads(prompt)
            task_id = json_data["task_id"]
            gaia_dataset_path = os.getenv("GAIA_DATASET_PATH", "./GAIA/2023")
            full_dataset = load_dataset_meta_dict(gaia_dataset_path)
            data_item = full_dataset[task_id]
            question = add_file_path(
                data_item, file_path=gaia_dataset_path
            )["Question"]
            send_output(f"\n```gaia_question\n{json.dumps(data_item, indent=2)}\n```")
        except Exception as e:
            pass

        if not question:
            logger.warning("Could not find GAIA question for prompt, chat using prompt directly!")
            send_output(
                f"\n```question\n{json.dumps(prompt, indent=2)}```\n"
            )
            question = prompt

        task = Task(input=question, agent=super_agent, conf=TaskConfig())

        last_output: Output = None
        rich_ui = MarkdownAworldUI()
        async for output in Runners.streamed_run_task(task).stream_events():
            print(f">>> Gaia Agent Event Ouput: {output}")
            res = await AworldUI.parse_output(output, rich_ui)
            for item in res if isinstance(res, list) else [res]:
                send_output(item)
                last_output = item

        if data_item and last_output:
            final_response = judge_answer(data_item, last_output)
            send_output(final_response)

    except Exception as e:
        logger.error(
            f"Error processing {args.prompt}, result: {result}, error: {traceback.format_exc()}"
        )


if __name__ == "__main__":
    asyncio.run(main())
