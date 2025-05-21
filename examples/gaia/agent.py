import argparse
import json
import logging
import os
import re
import signal
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

from dotenv import load_dotenv
from tabulate import tabulate

from aworld.config.conf import AgentConfig, ConfigDict, TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.common import ActionModel, Observation
from aworld.core.task import Task
from aworld.logs.util import Color
from aworld.memory.base import MemoryItem
from aworld.models.llm import call_llm_model
from aworld.models.model_response import ToolCall
from aworld.output.base import StepOutput
from aworld.runner import Runners
from aworld.utils.common import sync_exec
from examples.gaia.utils import question_scorer, setup_logger


def cleanup(func: Callable) -> Callable:
    """
    A decorator that ensures results are saved even if the program is interrupted.
    It handles keyboard interrupts and other exceptions by saving results before exiting.
    """

    def wrapper(self: GaiaRunner, *args, **kwargs):
        def signal_handler(sig, frame):
            self.logger.info("Received interrupt signal. Saving results before exit...")
            if self.split == "validation":
                self._report_results(self.results)
            self._save_results()
            sys.exit(0)

        # Register the signal handler for keyboard interrupt
        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal_handler)

        try:
            # Execute the wrapped function
            return func(self, *args, **kwargs)
        except Exception as e:
            # Log the exception and save results
            self.logger.error(f"Exception occurred: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            self.logger.info("Saving results before raising exception...")
            raise
        finally:
            # Save results and restore original signal handler
            if self.split == "validation":
                self._report_results(self.results)
            self._save_results()
            signal.signal(signal.SIGINT, original_handler)

    return wrapper


class GaiaAgent(Agent):
    def __init__(
        self,
        output_folder_path: str,
        config: Union[Dict[str, Any], ConfigDict, AgentConfig],
        resp_parse_func: Callable[..., Any] = None,
        **kwargs,
    ):
        super().__init__(config, resp_parse_func, **kwargs)
        self.logger: logging.Logger = self._setup_logger(
            self.__class__.__name__, output_folder_path
        )

    def _setup_logger(
        self, logger_name: str, output_folder_path: str, file_name: str = "main.log"
    ) -> logging.Logger:
        return setup_logger(
            logger_name=logger_name,
            output_folder_path=output_folder_path,
            file_name=file_name,
        )

    def policy(
        self, observation: Observation, info: Dict[str, Any] = {}, **kwargs
    ) -> Union[List[ActionModel], None]:
        """Adapted from the base class. Format necessary GAIA logs.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """
        # LOG CKPT: Agent's Observation
        self._color_log(f"💡 {self.name} Observation: {observation}", Color.green)

        if kwargs.get("output") and isinstance(kwargs.get("output"), StepOutput):
            output = kwargs["output"]

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
            output_prompt=self.output_prompt,
        )

        # logging_level=DEBUG
        self._log_messages(messages)
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
            self.logger.info(f"Execute response: {llm_response.message}")
        except Exception as e:
            self.logger.warning(traceback.format_exc())
            raise e
        finally:
            if llm_response:
                if llm_response.error:
                    self.logger.error(f"llm result error: {llm_response.error}")
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
        if not agent_result.is_call_tool:
            self._finished = True
        # output.mark_finished()

        actions: List[ActionModel] = agent_result.actions

        # LOG CKPT: Agent's Policy
        self._color_log(f"💡 {self.name} Policy: {actions}", Color.cyan)

        return actions

    def _color_log(self, value: str, color: Color):
        self.logger.info(f"{color} {value} {Color.reset}")

    def _log_messages(self, messages: List[Dict[str, Any]]) -> None:
        """Log the sequence of messages for debugging purposes"""
        self.logger.debug(f"[agent] Invoking LLM with {len(messages)} messages:")
        for i, msg in enumerate(messages):
            prefix = msg.get("role")
            self.logger.debug(
                f"[agent] Message {i + 1}: {prefix} ==================================="
            )
            if isinstance(msg["content"], list):
                for item in msg["content"]:
                    if item.get("type") == "text":
                        self.logger.debug(f"[agent] Text content: {item.get('text')}")
                    elif item.get("type") == "image_url":
                        image_url: str = item.get("image_url", {}).get("url", "")
                        if image_url.startswith("data:image"):
                            self.logger.debug(f"[agent] Image: [Base64 image data]")
                        else:
                            self.logger.debug(f"[agent] Image URL: {image_url[:30]}...")
            else:
                content = str(msg["content"])
                chunk_size = 500
                for j in range(0, len(content), chunk_size):
                    chunk = content[j : j + chunk_size]
                    if j == 0:
                        self.logger.debug(f"[agent] Content: {chunk}")
                    else:
                        self.logger.debug(f"[agent] Content (continued): {chunk}")

            if "tool_calls" in msg and msg["tool_calls"]:
                for tool_call in msg.get("tool_calls"):
                    if isinstance(tool_call, dict):
                        self.logger.debug(
                            f"[agent] Tool call: {tool_call.get('name')} - ID: {tool_call.get('id')}"
                        )
                        args = str(tool_call.get("args", {}))[:1000]
                        self.logger.debug(f"[agent] Tool args: {args}...")
                    elif isinstance(tool_call, ToolCall):
                        self.logger.debug(
                            f"[agent] Tool call: {tool_call.function.name} - ID: {tool_call.id}"
                        )
                        args = str(tool_call.function.arguments)[:1000]
                        self.logger.debug(f"[agent] Tool args: {args}...")


class GaiaRunner(object):
    def __init__(
        self,
        *,
        dataset_folder_path: str,
        output_folder_path: str,
        agent: GaiaAgent,
        split: str,
        q: str = None,
        slice: str = None,
        blacklist_file_path: str = None,
        skip: bool = False,
        **kwargs,
    ):
        """
        Command Line Arguments:
            --split: Split of the dataset, e.g., validation, test.
            --q: Question Index, e.g., 0-0-0-0-0.
            --slice: A continuous range of question indices, e.g., 0:300
            --blacklist_file_path: Blacklist file path, e.g., blacklist.txt
            --skip: Skip the question if it has been processed before.
        """
        super().__init__(**kwargs)
        assert os.path.exists(dataset_folder_path), "dataset folder path not exists"
        assert split in ["validation", "test"], "split must be validation or test"
        assert (
            q is not None or slice is not None
        ), "Please provide either --q or --slice argument."

        if not os.path.exists(output_folder_path):
            os.makedirs(output_folder_path)

        self.output_folder_path: str = output_folder_path
        self.logger: logging.Logger = self._setup_logger(
            logger_name=self.__class__.__name__, output_folder_path=output_folder_path
        )

        self.complete_dataset: List[Dict[str, Any]] = self._construct_dataset(
            dataset_folder_path, split
        )
        self.target_dataset: List[Dict[str, Any]] = self._filter_dataset(
            q, slice, blacklist_file_path
        )
        self.skip: bool = skip
        self.split: str = split

        self.results: List[Dict[str, Any]] = self._read_existing_results()
        self.agent: GaiaAgent = agent

    def _setup_logger(
        self, logger_name: str, output_folder_path: str, file_name: str = "main.log"
    ) -> logging.Logger:
        return setup_logger(
            logger_name=logger_name,
            output_folder_path=output_folder_path,
            file_name=file_name,
        )

    def _construct_dataset(
        self, path: str, split: str = "validation"
    ) -> List[Dict[str, Any]]:
        data_dir = Path(path) / split
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
                task["Question"] = self._add_file_path(task, data_dir)
        return dataset

    def _add_file_path(self, task: Dict[str, Any], data_dir: Path) -> str:
        file_path: Path = data_dir / task["file_name"]
        if file_path.suffix in [".pdf", ".docx", ".doc", ".txt"]:
            question = (
                task["Question"]
                + f" Here are the necessary document files: {file_path}"
            )

        elif file_path.suffix in [".jpg", ".jpeg", ".png"]:
            question = (
                task["Question"] + f" Here are the necessary image files: {file_path}"
            )

        elif file_path.suffix in [".xlsx", "xls", ".csv"]:
            question = task["Question"] + (
                f" Here are the necessary table files: {file_path}, for processing excel file,"
                " you can use the excel tool or write python code to process the file"
                " step-by-step and get the information."
            )
        elif file_path.suffix in [".py"]:
            question = (
                task["Question"] + f" Here are the necessary python files: {file_path}"
            )
        else:
            question = task["Question"] + f" Here are the necessary files: {file_path}"
        return question

    def _filter_dataset(
        self, q: str = None, slice: str = None, blacklist_file_path: str = None
    ) -> List[Dict[str, Any]]:
        """
        Filter the dataset based on the command line arguments.
        """
        if blacklist_file_path is not None:
            assert os.path.exists(
                blacklist_file_path
            ), f"Blacklist file path {blacklist_file_path} not exists"
            with open(blacklist_file_path, "r", encoding="utf-8") as f:
                blacklist: Set[str] = set(f.read().splitlines())
        else:
            blacklist: Set[str] = set()

        try:
            # Default setting where user want to run full dataset
            if q is None and slice is None:
                return [
                    task
                    for task in self.complete_dataset
                    if task["task_id"] not in blacklist
                ]
            # Specify question index. Highest priority: override other arguments if provided.
            if q is not None:
                if q not in [task["task_id"] for task in self.complete_dataset]:
                    raise ValueError(f"Question {q} not found in dataset.")
                if q in blacklist:
                    raise ValueError(f"Question {q} is in blacklist.")
                return [task for task in self.complete_dataset if task["task_id"] == q]
            # Take a slice of the dataset.
            if slice is not None:
                start, end = slice.split(":")
                start = int(start)
                end = int(end)
                if start < 0 or end > len(self.complete_dataset):
                    raise ValueError(
                        f"Invalid slice range: {slice}. Must be in the range of 0-{len(self.complete_dataset)}."
                    )
                return [
                    task
                    for task in self.complete_dataset[start:end]
                    if task["task_id"] not in blacklist
                ]
        except Exception:
            self.logger.error(f"Error filtering dataset: {traceback.format_exc()}")
            self.logger.warning(
                "Error filtering dataset. Returning full dataset instead."
            )
            return self.complete_dataset

    def _read_existing_results(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        output_file = os.path.join(self.output_folder_path, "results.jsonl")
        if os.path.exists(output_file):
            with open(output_file, "r", encoding="utf-8") as f:
                for line in f.readlines():
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        self.logger.error(
                            f"Error reading existing results: {traceback.format_exc()}"
                        )
                        self.logger.error(f"Original line is: {line}")
            return results
        else:
            return results

    @cleanup
    def submit(self) -> None:
        """
        Driver method to submit the tasks to the agent and evaluate the results if possible.
        """
        for task in self.target_dataset:
            if self.skip:
                if any(result["task_id"] == task["task_id"] for result in self.results):
                    self.logger.info(f"Skipping task {task['task_id']}")
                    continue

            self.logger.info(
                "=" * 25 + f" <START> {task['task_id']} <START/> " + "=" * 25
            )

            task_id = task["task_id"]
            question = task["Question"]

            result: Dict[str, Dict[str, Any]] = Runners.sync_run_task(
                task=Task(
                    input=question, agent=self.agent, conf=TaskConfig(task_id=task_id)
                )
            )

            match: Optional[re.Match] = re.search(
                r"<answer>(.*?)</answer>", result["task_0"]["answer"]
            )

            if match:
                answer: str = match.group(1)
                self.logger.info(f"Agent answer: {answer}")
            else:
                answer: Optional[str] = None
                self.logger.error(
                    f"Failed to get answer! Original output: {result['task_0']['answer']}"
                )

            # evaluate the answer for validation set
            if self.split == "validation":
                evaluation: bool = question_scorer(answer, task["Final answer"])
                self.logger.info(f"Correct answer: {task['Final answer']}")
                self.logger.info(f"Question {task_id}: {evaluation}")
                new_result = {
                    "task_id": task_id,
                    "level": task["Level"],
                    "question": question,
                    "answer": task["Final answer"],
                    "model_answer": answer or "",
                    "is_correct": question_scorer(answer, task["Final answer"]),
                }
            elif self.split == "test":
                new_result = {
                    "task_id": task_id,
                    "level": task["Level"],
                    "question": question,
                    "model_answer": answer or "",
                }

            # append if not exists; update otherwise
            self._update_results(new_result)

            self.logger.info("=" * 25 + f" <END> {task['task_id']} <END/> " + "=" * 25)

    def _update_results(self, new_result: Dict[str, Any]) -> None:
        task_id = new_result["task_id"]

        # Check if this task_id already exists in results
        existing_index: Optional[int] = next(
            (
                i
                for i, result in enumerate(self.results)
                if result["task_id"] == task_id
            ),
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
        output_file = os.path.join(self.output_folder_path, "results.jsonl")
        temp_file = os.path.join(self.output_folder_path, "results.jsonl.tmp")

        try:
            # Write to a temporary file first to avoid corruption if interrupted
            with open(temp_file, "w", encoding="utf-8") as f:
                for result in self.results:
                    f.write(json.dumps(result) + "\n")

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
                        for result in self.results:
                            f.write(json.dumps(result) + "\n")
                    self.logger.info(
                        f"Results saved to {output_file} (fallback method)"
                    )
                except Exception as e2:
                    self.logger.error(f"Fallback save also failed: {str(e2)}")

    def _report_results(self, entries: List[Dict[str, Any]]) -> None:
        # Initialize counters
        total_entries = len(entries)
        total_correct = 0

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
            level_table.append(
                [level, stats["total"], stats["correct"], f"{stats['accuracy']:.2f}%"]
            )
        self.logger.info(tabulate(level_table, headers=headers, tablefmt="grid"))


if __name__ == "__main__":
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
        "--q",
        type=str,
        nargs="?",
        help=(
            "Question Index, e.g., 0-0-0-0-0."
            " Highest priority: override other arguments if provided."
        ),
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
    args = parser.parse_args()

    agent = GaiaAgent()

    dataset_path = os.getenv("GAIA_DATASET_PATH")
    log_path = os.getenv("LOG_PATH")
    runner = GaiaRunner(
        # dataset
        dataset_folder_path=dataset_path,
        # workdir: logs & files
        output_folder_path=log_path,
        # agent
        agent=agent,
        # runner config
        split=args.split,
        q=args.q,
        slice=args.slice,
        blacklist_file_path=args.blacklist_file_path,
        skip=args.skip,
    )
