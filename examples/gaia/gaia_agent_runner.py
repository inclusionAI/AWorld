import argparse
import asyncio
import copy
import json
import logging
import os
import re
import signal
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Union

from dotenv import load_dotenv
from tabulate import tabulate

from aworld.config.conf import AgentConfig, ConfigDict, TaskConfig
from aworld.core.agent.llm_agent import Agent
from aworld.core.common import ActionModel, Observation
from aworld.core.memory import MemoryItem
from aworld.core.task import Task
from aworld.logs.util import Color
from aworld.models.llm import call_llm_model
from aworld.models.model_response import ToolCall
from aworld.output.base import Output, StepOutput
from aworld.output.ui.base import AworldUI
from aworld.output.ui.markdown_aworld_ui import MarkdownAworldUI
from aworld.runner import Runners
from aworld.utils.common import sync_exec
from examples.gaia.prompt import system_prompt
from examples.gaia.utils import (
    add_file_path,
    color_log,
    load_dataset_meta_dict,
    question_scorer,
    setup_logger,
)

# pylint: disable=C0301,C0325
logger = logging.getLogger(__name__)


@dataclass
class RunnerArguments:
    """
    Command Line Arguments:
        --split: Split of the dataset, e.g., validation, test.
        --level: Level of the dataset, e.g., 1, 2, 3.
        --q: Question Index, e.g., 0-0-0-0-0.
        --slice: A continuous range of question indices, e.g., 0:300
        --blacklist_file_path: Blacklist file path, e.g., blacklist.txt
        --skip: Skip the question if it has been processed before.
        --submit: Whether to generate the submission file for GAIA leaderboard.
    """

    split: Literal["validation", "test"]
    level: List = None
    q: str = None
    slice: str = None
    blacklist_file_path: str = None
    skip: bool = False
    retry: bool = False
    submit: bool = False
    task_timeout: int = 20 * 60


class GaiaTimeoutException(Exception):
    """GaiaTimeoutException"""


class GaiaAgent(Agent):
    def __init__(
        self,
        output_folder_path: str,
        config: Union[Dict[str, Any], ConfigDict, AgentConfig],
        resp_parse_func: Callable[..., Any] = None,
        **kwargs,
    ):
        super().__init__(config, resp_parse_func, **kwargs)
        self.truncated_length = 1000
        self.logger: logging.Logger = self._setup_logger(
            logger_name=self.__class__.__name__, output_folder_path=output_folder_path
        )
        self._color_log(f"Using {os.getenv('LLM_MODEL_NAME')} from {os.getenv('LLM_BASE_URL')}", Color.red)

    def policy(self, observation: Observation, info: Dict[str, Any] = None, **kwargs) -> Union[List[ActionModel], None]:
        """Adapted from the base class. Format necessary GAIA logs.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """
        # LOG CKPT: Agent's Observation
        _log_obs = copy.deepcopy(observation)
        if len(_log_obs.content) > self.truncated_length:
            _log_obs.content = _log_obs.content[: self.truncated_length] + "..."
        self._color_log(f"🌍 Observation: {_log_obs}", Color.pink)

        if info is None:
            info = {}

        if kwargs.get("output") and isinstance(kwargs.get("output"), StepOutput):
            output = kwargs["output"]  # pylint: disable=W0612

        self._finished = False
        self.desc_transform()
        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
        messages = self.messages_transform(
            content=observation.content,
            image_urls=images,
            sys_prompt=self.system_prompt,
            agent_prompt=self.agent_prompt,
        )

        self.memory.add(
            MemoryItem(
                content=messages[-1]["content"],
                metadata={
                    "role": messages[-1]["role"],
                    "agent_name": self.name(),
                    "tool_call_id": messages[-1].get("tool_call_id"),
                },
            )
        )

        llm_response = None
        try:
            llm_response = call_llm_model(
                self.llm,
                messages=messages,
                model=self.model_name,
                temperature=self.conf.llm_config.llm_temperature,
                tools=self.tools if self.tools else None,
            )
            self._color_log(f"🤖 Execute response: {llm_response.message}", Color.orange)
        except Exception as e:
            self.logger.warning(traceback.format_exc())
            raise e
        finally:
            if llm_response:
                if llm_response.error:
                    self.logger.error(f"LLM result error: {llm_response.error}")
                else:
                    self.memory.add(
                        MemoryItem(
                            content=llm_response.content,
                            metadata={
                                "role": "assistant",
                                "agent_name": self.name(),
                                "tool_calls": llm_response.tool_calls,
                            },
                        )
                    )
            else:
                self.logger.error(f"{self.name()} failed to get LLM response")
                raise RuntimeError(f"{self.name()} failed to get LLM response")

        # output.add_part(MessageOutput(source=llm_response, json_parse=False))
        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool and "<answer>" in llm_response.content:
            self._finished = True
        # output.mark_finished()

        actions: List[ActionModel] = agent_result.actions

        # LOG CKPT: Agent's Policy
        self._color_log(f"💡 Policy: {actions}", Color.cyan)

        return actions

    def _setup_logger(self, logger_name: str, output_folder_path: str, file_name: str = "app.log") -> logging.Logger:
        return setup_logger(
            logger_name=logger_name,
            output_folder_path=output_folder_path,
            file_name=file_name,
        )

    def _color_log(self, message: str, color: Color) -> None:
        color_log(self.logger, message, color)

    def _log_messages(self, messages: List[Dict[str, Any]]) -> None:
        """Log the sequence of messages for debugging purposes"""
        self.logger.debug(f"[agent] Invoking LLM with {len(messages)} messages:")
        for i, msg in enumerate(messages):
            prefix = msg.get("role")
            self.logger.debug(f"[agent] Message {i + 1}: {prefix} ===================================")
            if isinstance(msg["content"], list):
                for item in msg["content"]:
                    if item.get("type") == "text":
                        self.logger.debug(f"[agent] Text content: {item.get('text')}")
                    elif item.get("type") == "image_url":
                        image_url: str = item.get("image_url", {}).get("url", "")
                        if image_url.startswith("data:image"):
                            self.logger.debug("[agent] Image: [Base64 image data]")
                        else:
                            self.logger.debug(f"[agent] Image URL: {image_url[:30]}...")
            else:
                content = str(msg["content"])
                self.logger.debug(f"[agent] Content: {content}")
            if "tool_calls" in msg and msg["tool_calls"]:
                for tool_call in msg.get("tool_calls"):
                    if isinstance(tool_call, dict):
                        self.logger.debug(f"[agent] Tool call: {tool_call.get('name')} - ID: {{tool_call.get('id')}}")
                        args = str(tool_call.get("args", {}))[:1000]
                        self.logger.debug(f"[agent] Tool args: {args}...")
                    elif isinstance(tool_call, ToolCall):
                        self.logger.debug(f"[agent] Tool call: {tool_call.function.name} - ID: {tool_call.id}")
                        args = str(tool_call.function.arguments)[:1000]
                        self.logger.debug(f"[agent] Tool args: {args}...")


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
            r"Gaia Agent Runner initialized: "
            f"super_agent={self.super_agent}, "
            f"gaia_dataset_path={self.gaia_dataset_path}, "
            f"full_dataset={len(self.full_dataset)}"
        )

    async def run(self, prompt: str):
        yield ("\n### GAIA Agent Start!")

        mcp_servers = "\n- ".join(self.super_agent.mcp_servers)
        yield (f"\n```gaia_agent_status\n- {mcp_servers}\n```\n")

        question = None
        data_item = None
        try:
            json_data = json.loads(prompt)
            task_id = json_data["task_id"]

            data_item = self.full_dataset[task_id]
            question = add_file_path(data_item, file_path=self.gaia_dataset_path)["Question"]
            yield (f"\n```gaia_question\n{json.dumps(data_item, indent=2)}\n```\n")
        except Exception:
            pass

        if not question:
            logger.warning("Could not find GAIA question for prompt, chat using prompt directly!")
            yield (f"\n{prompt}\n")
            question = prompt

        try:
            task = Task(
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

        except Exception:
            logger.error(f"Error processing {prompt}, error: {traceback.format_exc()}")

    def _judge_answer(self, data_item: dict, result: Output):
        answer = result
        match: re.Match = re.search(r"<answer>(.*?)</answer>", answer)
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


class GaiaRunner:
    def __init__(
        self,
        *,
        # agent: GaiaAgent,
        aworld_runner: GaiaAgentRunner,
        runner_args: RunnerArguments,
        dataset_folder_path: str,
        output_folder_path: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        assert os.path.exists(dataset_folder_path), "dataset folder path not exists"
        assert runner_args.split in ["validation", "test"], "split must be validation or test"
        assert runner_args.q is not None or runner_args.slice is not None or runner_args.level, (
            "Please provide one of --q or --slice or --level argument."
        )

        # self.agent: GaiaAgent = agent
        self.aworld_runner: GaiaAgentRunner = aworld_runner  # for AWorld display compatibility
        self.runner_args: RunnerArguments = runner_args
        self.dataset_folder_path: str = dataset_folder_path
        self.output_folder_path: str = output_folder_path

        self.task_timeout: int = runner_args.task_timeout

        if not os.path.exists(output_folder_path):
            os.makedirs(output_folder_path)
        self.output_folder_path: str = output_folder_path
        self.logger: logging.Logger = self._setup_logger(
            logger_name=self.__class__.__name__, output_folder_path=output_folder_path
        )

        if os.getenv("FRAMEWORK_LOG_LEVEL") != "INFO":
            self.logger.info(f"{os.getenv('FRAMEWORK_LOG_LEVEL')=}")

        self._color_log("🏃 GaiaRunner Initialization", Color.bold)
        self.complete_dataset: List[Dict[str, Any]] = self._construct_dataset()
        self.target_dataset: List[Dict[str, Any]] = self._filter_dataset()
        self.results: List[Dict[str, Any]] = self._read_existing_results()
        self.retry_ids: Set[str] = self._filter_retry_ids()
        self._color_log(f"📖 Fetched {len(self.complete_dataset)} tasks.", Color.bold)
        self._color_log(f"🧯 Filtered {len(self.target_dataset)} tasks.", Color.bold)
        self._color_log(f"💯 Read {len(self.results)} existing results.", Color.bold)
        self._color_log(f"💪 Retry {len(self.retry_ids)} error results.", Color.bold)

    @staticmethod
    def cleanup(func: Callable) -> Callable:
        """
        A decorator that ensures results are saved even if the program is interrupted.
        It handles keyboard interrupts and other exceptions by saving results before exiting.
        """

        def wrapper(self: "GaiaRunner", *args, **kwargs):
            async def async_wrapper():
                try:
                    # Execute the wrapped function
                    return await func(self, *args, **kwargs)
                except Exception as e:
                    # Log the exception and save results
                    self.logger.error(f"Exception occurred: {str(e)}")
                    self.logger.error(f"Traceback: {traceback.format_exc()}")
                    self.logger.info("Saving results before raising exception...")
                    raise
                finally:
                    # Save results before exiting
                    if self.runner_args.split == "validation":
                        self._report_results(self.results)
                    self._save_results()
                    if self.runner_args.submit:
                        self._export_submission()
                    # exit the program
                    sys.exit(0)

            return async_wrapper()

        return wrapper

    @staticmethod
    def timeout(seconds: int):
        def _raise_timeout_exception(signum, frame):
            raise GaiaTimeoutException("Gaia Task execution timed out")

        def decorator(func):
            def wrapper(self, *args, **kwargs):
                # Store the original handler to restore it later
                original_handler = signal.signal(signal.SIGALRM, _raise_timeout_exception)
                # Set the alarm for the specified timeout duration
                signal.alarm(seconds)
                try:
                    # Execute the wrapped function
                    return func(self, *args, **kwargs)
                finally:
                    # Cancel the alarm and restore the original signal handler
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, original_handler)

            return wrapper

        return decorator

    @cleanup
    async def submit(self) -> None:
        """
        Driver method to submit the tasks to the agent and evaluate the results if possible.
        """
        self._color_log("🎯 Task Submitted~~~", Color.red)
        for task in self.target_dataset:
            if self.runner_args.skip and any(result["task_id"] == task["task_id"] for result in self.results):
                if task["task_id"] in self.retry_ids:
                    self.logger.info(f"🔄 Retrying task {task['task_id']}...")
                else:
                    self.logger.debug(f"⏭️ Skipping task {task['task_id']}...")
                    continue

            self._color_log("=" * 20 + f" <START> {task['task_id']} <START/> " + "=" * 20, Color.darkgrey)
            self._color_log(f"❓ Question: {task['Question']}", Color.lightblue)
            self._color_log(f"🪜 Level: {task['Level']}", Color.lightblue)
            try:
                result: Output = await self._async_execute_task(task=task)
                answer: Optional[str] = self._extract_answer(result)
                self._update_results(task, answer)
            except GaiaTimeoutException:
                self.logger.error(f"Task {task['task_id']} timed out after {self.task_timeout} seconds.")
                self._update_results(task, answer="<TIMEOUT: 20>")
            except Exception:
                self.logger.error(f"Error executing task {task['task_id']}: {traceback.format_exc()}")
                self._update_results(task, answer="<ERROR>")
            self._color_log("=" * 20 + f" <END> {task['task_id']} <END/> " + "=" * 20, Color.darkgrey)
        self._color_log("🎉 Task Finished~~~", Color.red)

    def _setup_logger(self, logger_name: str, output_folder_path: str, file_name: str = "app.log") -> logging.Logger:
        return setup_logger(
            logger_name=logger_name,
            output_folder_path=output_folder_path,
            file_name=file_name,
        )

    def _color_log(self, message: str, color: Color) -> None:
        color_log(self.logger, message, color)

    def _construct_dataset(self) -> List[Dict[str, Any]]:
        def _add_file_path(task: Dict[str, Any], data_dir: Path) -> str:
            file_path: Path = data_dir / task["file_name"]
            if file_path.suffix in [".pdf", ".docx", ".doc", ".txt"]:
                question = task["Question"] + f" Here are the necessary document files: {file_path}."
            elif file_path.suffix in [".jpg", ".jpeg", ".png"]:
                question = task["Question"] + f" Here are the necessary image files: {file_path}."
            elif file_path.suffix in [".xlsx", "xls", ".csv"]:
                question = task["Question"] + (f" Here are the necessary spreadsheet files: {file_path}.")
            elif file_path.suffix in [".py"]:
                question = task["Question"] + f" Here are the necessary python files: {file_path}."
            else:
                question = task["Question"] + f" Here are the necessary files: {file_path}."
            return question

        data_dir = Path(self.dataset_folder_path) / self.runner_args.split
        data_file = data_dir / "metadata.jsonl"
        assert data_file.exists(), f"{data_file} not exists"

        dataset: List[Dict[str, Any]] = []
        with open(data_file, "r", encoding="utf-8") as metaf:
            lines = metaf.readlines()
            for line in lines:
                data = json.loads(line)
                if data["task_id"] == "0-0-0-0-0":
                    continue
                if data["file_name"]:
                    data["file_name"] = data_dir / data["file_name"]
                dataset.append(data)

        for task in dataset:
            if task["file_name"]:
                task["Question"] = _add_file_path(task, data_dir)
            task["Question"] += " Please solve the task as best as you can. Now, let's start!"
        return dataset

    def _filter_dataset(self) -> List[Dict[str, Any]]:
        """
        Filter the dataset based on the command line arguments.
        """
        if self.runner_args.blacklist_file_path is not None:
            assert os.path.exists(self.runner_args.blacklist_file_path), (
                f"Blacklist file path {self.runner_args.blacklist_file_path} not exists"
            )
            with open(self.runner_args.blacklist_file_path, "r", encoding="utf-8") as f:
                blacklist: Set[str] = set(f.read().splitlines())
        else:
            blacklist: Set[str] = set()

        try:
            # Default setting where user want to run full dataset
            if self.runner_args.q is None and self.runner_args.slice is None and self.runner_args.level is None:
                return list(task for task in self.complete_dataset if task["task_id"] not in blacklist)
            # Specify question index. Highest priority: override other arguments if provided.
            if self.runner_args.q is not None:
                if self.runner_args.q not in [task["task_id"] for task in self.complete_dataset]:
                    raise ValueError(f"Question {self.runner_args.q} not found in dataset.")
                if self.runner_args.q in blacklist:
                    raise ValueError(f"Question {self.runner_args.q} is in blacklist.")
                return list(task for task in self.complete_dataset if task["task_id"] == self.runner_args.q)
            # Specify the level of questions.
            if self.runner_args.level is not None:
                return list(
                    task
                    for task in self.complete_dataset
                    if task["Level"] in self.runner_args.level and task["task_id"] not in blacklist
                )
            # Take a slice of the dataset.
            if self.runner_args.slice is not None:
                start, end = self.runner_args.slice.split(":")
                start = int(start)
                end = int(end)
                if start < 0 or end > len(self.complete_dataset):
                    raise ValueError(
                        f"Invalid slice range: {self.runner_args.slice}. "
                        f"Must be in the range of 0-{{len(self.complete_dataset)}}."
                    )
                return list(task for task in self.complete_dataset[start:end] if task["task_id"] not in blacklist)
        except Exception:
            self.logger.error(f"Error filtering dataset: {traceback.format_exc()}")
            self.logger.warning("Error filtering dataset. Returning full dataset instead.")
            return list(self.complete_dataset)

    def _read_existing_results(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        output_file = os.path.join(self.output_folder_path, "results.json")
        if os.path.exists(output_file):
            with open(output_file, "r", encoding="utf-8") as f:
                try:
                    results = json.loads(f.read())
                except json.JSONDecodeError:
                    self.logger.error(f"Error reading existing results: {traceback.format_exc()}")
                    self.logger.error(f"Original file path is: {output_file}. Check it carefully!")
        return results

    def _filter_retry_ids(self) -> Set[str]:
        """
        Filter the dataset whose `model_answer` is marked as <ERROR> or <TIMEOUT: 20>.
        """
        if self.runner_args.retry:
            return set(task["task_id"] for task in self.results if task["model_answer"] in ["<ERROR>", "<TIMEOUT: 20>"])
        return set()

    async def _async_execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        result = None
        async for output in self.aworld_runner.run(prompt=json.dumps(task)):
            result = output  # Keep updating with the latest output

        if not result:
            self.logger.warning(f"⚠️ Task {task['task_id']} with EMPTY return!")
        return result

    def _extract_answer(self, output: Output) -> str:
        match: Optional[re.Match] = re.search(r"<answer>(.*?)</answer>", output)
        if match:
            answer: str = match.group(1)
            self._color_log(f"Agent answer: {answer}", Color.green)
        else:
            answer: Optional[str] = None
            self.logger.error(f"Failed to get answer! Original output: {output}")
        return answer

    def _update_results(self, task: Dict[str, Any], answer: Optional[str]) -> None:
        # execution failed
        if answer is None:
            return

        task_id = task["task_id"]
        question = task["Question"]

        # evaluate the answer for validation set
        if self.runner_args.split == "validation":
            evaluation: bool = question_scorer(answer, task["Final answer"])
            self._color_log(f"Correct answer: {task['Final answer']}", Color.green)
            self._color_log(f"Question {task_id}: {evaluation}", Color.green)
            new_result = {
                "task_id": task_id,
                "level": task["Level"],
                "question": question,
                "answer": task["Final answer"],
                "model_answer": answer or "",
                "is_correct": question_scorer(answer, task["Final answer"]),
            }
        elif self.runner_args.split == "test":
            new_result = {
                "task_id": task_id,
                "level": task["Level"],
                "question": question,
                "model_answer": answer or "",
            }
        else:
            raise ValueError("split must be one of `validation` and `test`")

        # Check if this task_id already exists in results
        existing_index: Optional[int] = next(
            (i for i, result in enumerate(self.results) if result["task_id"] == task_id),
            None,
        )
        if existing_index is not None:
            # Update existing record
            self.results[existing_index] = new_result
            self.logger.info(f"Updated existing record for task_id: {task_id}")
        else:
            # Append new record
            self.results.append(new_result)
            self.logger.info(f"Added new record for task_id: {task_id}")

    def _save_results(self) -> None:
        """
        Save the current results to the output folder.
        This method is called by the cleanup decorator to ensure results are saved
        even if the program is interrupted.
        """
        output_file = os.path.join(self.output_folder_path, "results.json")
        temp_file = os.path.join(self.output_folder_path, "results.json.tmp")

        try:
            # Write to a temporary file first to avoid corruption if interrupted
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.results, indent=2, ensure_ascii=False))

            # Rename the temporary file to the actual output file
            os.replace(temp_file, output_file)
            self.logger.info(f"Results saved to {output_file}")
        except Exception as e:
            self.logger.error(f"Error saving results: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")

            # Try a direct write as a fallback
            if not os.path.exists(output_file):
                try:
                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(json.dumps(self.results, indent=2, ensure_ascii=False))
                    self.logger.info(f"Results saved to {output_file} (fallback method)")
                except Exception as e2:
                    self.logger.error(f"Fallback save also failed: {str(e2)}")

    def _report_results(self, entries: List[Dict[str, Any]]) -> None:
        # Initialize counters
        total_correct = 0
        total_entries = len(entries)
        if not total_entries or total_entries == 0:
            self.logger.info("No results to report.")
            return

        # Initialize level statistics
        level_stats = {}

        # Process each entry
        for entry in entries:
            level = entry.get("level")
            is_correct = entry.get("is_correct", False)

            # Initialize level stats if not already present
            if level not in level_stats:
                level_stats[level] = {"total": 0, "correct": 0, "accuracy": 0}

            # Update counters
            level_stats[level]["total"] += 1
            if is_correct:
                total_correct += 1
                level_stats[level]["correct"] += 1

        # Calculate accuracy for each level
        for level, stats in level_stats.items():
            if stats["total"] > 0:
                stats["accuracy"] = (stats["correct"] / stats["total"]) * 100

        # Print overall statistics with colorful logging
        self.logger.info("Overall Statistics:")
        overall_accuracy = (total_correct / total_entries) * 100

        # Create overall statistics table
        overall_table = [
            ["Total Entries", total_entries],
            ["Total Correct", total_correct],
            ["Overall Accuracy", f"{overall_accuracy:.2f}%"],
        ]
        self.logger.info(tabulate(overall_table, tablefmt="grid"))

        # Create level statistics table
        self.logger.info("Statistics by Level:")
        level_table = []
        headers = ["Level", "Total Entries", "Correct Answers", "Accuracy"]

        for level in sorted(level_stats.keys()):
            stats = level_stats[level]
            level_table.append([level, stats["total"], stats["correct"], f"{stats['accuracy']:.2f}%"])
        self.logger.info(tabulate(level_table, headers=headers, tablefmt="grid"))

    def _export_submission(self) -> None:
        """
        Export the results to a submission file.
        """
        # indexing results by task_id for easier lookup in submission
        results: Dict[str, Dict[str, Any]] = {result["task_id"]: result for result in self.results}
        # entire submission sets
        task_ids: List[str] = [task["task_id"] for task in self.complete_dataset]
        # crafting submission, is_correct if self.split is `validation`
        submission: List[Dict[str, Any]] = [
            {
                "task_id": task_id,
                "model_answer": results.get(task_id, {}).get("model_answer", ""),
                "is_correct": results.get(task_id, {}).get("is_correct", None),
                "reasoning_trace": "",
            }
            for task_id in task_ids
        ]
        # dump to jsonl file
        with open(Path(self.output_folder_path) / "submission.jsonl", "w", encoding="utf-8") as f:
            for item in submission:
                f.write(json.dumps(item) + "\n")
            f.write(json.dumps({"task_id": "0-0-0-0-0", "model_answer": "", "reasoning_trace": ""}) + "\n")
        self._color_log("🎉 Submission file generated!", Color.red)


if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--split",
        type=str,
        default="validation",
        choices=["validation", "test"],
        help="Split of the dataset, e.g., validation, test",
    )
    parser.add_argument(
        "--skip",
        action="store_true",
        help="Skip the question if it has been processed before.",
    )
    parser.add_argument(
        "--level",
        type=lambda value: [int(digit) for digit in value.split(",")],
        nargs="?",
        help="Level of the question, e.g., 1, 2, 3",
    )
    parser.add_argument(
        "--q",
        type=str,
        nargs="?",
        help=("Question Index, e.g., 0-0-0-0-0. Highest priority: override other arguments if provided."),
    )
    parser.add_argument(
        "--slice",
        type=str,
        nargs="?",
        help="A continuous range of question indices, e.g., 0:300",
    )
    parser.add_argument(
        "--blacklist_file_path",
        type=str,
        nargs="?",
        help="Blacklist file path, e.g., blacklist.txt",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Dump the submission result",
    )
    parser.add_argument(
        "--retry",
        action="store_true",
        help="Retry the question if it fails (marked as <ERROR>)",
    )
    args = parser.parse_args()

    dataset_path = os.getenv("GAIA_DATASET_PATH")
    log_path = os.getenv("LOG_FILE_PATH")
    agent = GaiaAgent(
        output_folder_path=log_path,
        name="gaia_agent",
        system_prompt=system_prompt,
        config=AgentConfig(
            llm_provider="openai",
            llm_model_name=os.getenv("LLM_MODEL_NAME", "gpt-4o"),
            llm_api_key=os.getenv("LLM_API_KEY", "your_openai_api_key"),
            llm_base_url=os.getenv("LLM_BASE_URL", "your_openai_base_url"),
        ),
        mcp_servers=[
            "e2b-server",
            "audio",
            "browser",
            "csv",
            "docx",
            "download",
            "excel",
            "image",
            "pdf",
            "pptx",
            "reasoning",
            "search",
            "terminal",
            "video",
            "wayback",
            "wikipedia",
            # "yahoo-finance",
            "youtube",
            # "vector-store",
            "txt",
        ],
    )
    runner = GaiaRunner(
        aworld_runner=GaiaAgentRunner(agent=agent),
        runner_args=RunnerArguments(
            split=args.split,
            level=args.level,
            q=args.q,
            slice=args.slice,
            blacklist_file_path=args.blacklist_file_path,
            skip=args.skip,
            retry=args.retry,
            submit=args.submit,
        ),
        dataset_folder_path=dataset_path,
        output_folder_path=log_path,
    )
    asyncio.run(runner.submit())

    # output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    # if not os.path.exists(output_dir):
    #     os.makedirs(output_dir)

    # output_file = os.path.join(output_dir, f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")

    # async def main():
    #     parser = argparse.ArgumentParser()
    #     parser.add_argument("--prompt", type=str, default="")
    #     args = parser.parse_args()

    #     try:
    #         prompt = args.prompt

    #         llm_provider = os.getenv("LLM_PROVIDER")
    #         llm_model_name = os.getenv("LLM_MODEL_NAME")
    #         llm_api_key = os.getenv("LLM_API_KEY")
    #         llm_base_url = os.getenv("LLM_BASE_URL")
    #         llm_temperature = float(os.getenv("LLM_TEMPERATURE", "0.0"))

    #         def send_output(output):
    #             with open(output_file, "a", encoding="utf-8") as f:
    #                 f.write(f"{output}\n")

    #         async for i in GaiaAgentRunner(
    #             llm_provider=llm_provider,
    #             llm_model_name=llm_model_name,
    #             llm_base_url=llm_base_url,
    #             llm_api_key=llm_api_key,
    #             llm_temperature=llm_temperature,
    #         ).run(prompt):
    #             send_output(i)
    #     except Exception:
    #         logger.error(f"Error processing {args.prompt}, error: {traceback.format_exc()}")

    # asyncio.run(main())
