import json
import logging
import os
import re
import traceback
import uuid
from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.llm_agent import Agent
from aworld.core.task import Task
from aworld.runner import Runners
from aworld.output.ui.base import AworldUI
from aworld.output.ui.markdown_aworld_ui import MarkdownAworldUI
from aworld.output.base import Output
from examples.gaia.utils import (
    add_file_path,
    load_dataset_meta_dict,
    question_scorer,
)
from examples.gaia.prompt import system_prompt

logger = logging.getLogger(__name__)


class GaiaAgentRunner:
    """
    Gaia Agent Runner
    """

    def __init__(
        self,
        llm_provider: str,
        llm_model_name: str,
        llm_base_url: str,
        llm_api_key: str,
        llm_temperature: float = 0.0,
        mcp_config: dict = {},
    ):
        self.agent_config = AgentConfig(
            llm_provider=llm_provider,
            llm_model_name=llm_model_name,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_temperature=llm_temperature,
        )

        self.super_agent = Agent(
            conf=self.agent_config,
            name="gaia_super_agent",
            system_prompt=system_prompt,
            mcp_config=mcp_config,
            mcp_servers=mcp_config.get("mcpServers", {}).keys(),
        )

        self.gaia_dataset_path = os.path.abspath(
            os.getenv(
                "GAIA_DATASET_PATH",
                os.path.join(os.getcwd(), "examples", "gaia", "GAIA", "2023"),
            )
        )
        self.full_dataset = load_dataset_meta_dict(self.gaia_dataset_path)
        logger.info(
            f"Gaia Agent Runner initialized: super_agent={self.super_agent}, agent_config={self.agent_config}, gaia_dataset_path={self.gaia_dataset_path}, full_dataset={len(self.full_dataset)}"
        )

    async def run(self, prompt: str):
        yield (f"\n### GAIA Agent Start!")

        mcp_servers = "\n- ".join(self.super_agent.mcp_servers)
        yield (f"\n```gaia_agent_status\n- {mcp_servers}\n```\n")

        question = None
        data_item = None
        try:
            json_data = json.loads(prompt)
            task_id = json_data["task_id"]

            data_item = self.full_dataset[task_id]
            question = add_file_path(data_item, file_path=self.gaia_dataset_path)[
                "Question"
            ]
            yield (f"\n```gaia_question\n{json.dumps(data_item, indent=2)}\n```\n")
        except Exception as e:
            pass

        if not question:
            logger.warning(
                "Could not find GAIA question for prompt, chat using prompt directly!"
            )
            yield (f"\n{prompt}\n")
            question = prompt

        try:
            task = Task(
                id=task_id + "." + uuid.uuid1().hex if task_id else uuid.uuid1().hex,
                input=question,
                agent=self.super_agent,
                event_driven=False,
                conf=TaskConfig(max_steps=20),
            )

            last_output: Output = None
            rich_ui = MarkdownAworldUI()
            async for output in Runners.streamed_run_task(task).stream_events():
                logger.info(f"Gaia Agent Ouput: {output}")
                res = await AworldUI.parse_output(output, rich_ui)
                for item in res if isinstance(res, list) else [res]:
                    yield item
                    last_output = item

            logger.info(f"Gaia Agent Last Output: {last_output}")

            if data_item and last_output:
                final_response = self._judge_answer(data_item, last_output)
                yield final_response

        except Exception as e:
            logger.error(f"Error processing {prompt}, error: {traceback.format_exc()}")

    def _judge_answer(self, data_item: dict, result: Output):
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
            correct = question_scorer(answer, data_item["Final answer"])
            new_result = {
                "task_id": data_item["task_id"],
                "level": data_item["Level"],
                "question": data_item["Question"],
                "answer": data_item["Final answer"],
                "response": answer,
                "is_correct": correct,
            }
            return f"\n## Final Result: {'✅' if correct else '❌'}\n \n```gaia_result\n{json.dumps(new_result, indent=2)}\n```"
        else:
            new_result = answer
            return f"\n## Final Result:\n \n```gaia_result\n{json.dumps(new_result, indent=2)}\n```"


if __name__ == "__main__":
    import asyncio
    import argparse
    from datetime import datetime

    logger = logging.getLogger(__name__)

    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    output_file = os.path.join(
        output_dir, f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )

    async def main():
        parser = argparse.ArgumentParser()
        parser.add_argument("--prompt", type=str, default="")
        args = parser.parse_args()

        try:
            prompt = args.prompt

            llm_provider = os.getenv("LLM_PROVIDER")
            llm_model_name = os.getenv("LLM_MODEL_NAME")
            llm_api_key = os.getenv("LLM_API_KEY")
            llm_base_url = os.getenv("LLM_BASE_URL")
            llm_temperature = os.getenv("LLM_TEMPERATURE", 0.0)

            def send_output(output):
                with open(output_file, "a") as f:
                    f.write(f"{output}\n")

            async for i in GaiaAgentRunner(
                llm_provider=llm_provider,
                llm_model_name=llm_model_name,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_temperature=llm_temperature,
            ).run(prompt):
                send_output(i)
        except Exception as e:
            logger.error(
                f"Error processing {args.prompt}, error: {traceback.format_exc()}"
            )

    asyncio.run(main())
