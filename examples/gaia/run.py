import argparse
import json
import logging
import os
import re
import traceback
from typing import Any, Dict, List

from dotenv import load_dotenv

from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.task import Task
from aworld.runner import Runners
from examples.gaia.prompt import system_prompt
from examples.gaia.utils import (
    add_file_path,
    load_dataset_meta,
    question_scorer,
    report_results,
)

parser = argparse.ArgumentParser()
parser.add_argument(
    "--start",
    type=int,
    default=0,
    help="Start index of the dataset",
)
parser.add_argument(
    "--end",
    type=int,
    default=165,
    help="End index of the dataset",
)
parser.add_argument(
    "--q",
    type=str,
    help="Question Index, e.g., 0-0-0-0-0. Highest priority: override other arguments if provided.",
)
parser.add_argument(
    "--skip",
    action="store_true",
    help="Skip the question if it has been processed before.",
)
parser.add_argument(
    "--split",
    type=str,
    default="validation",
    help="Split of the dataset, e.g., validation, test",
)
parser.add_argument(
    "--blacklist_file_path",
    type=str,
    nargs="?",
    help="Blacklist file path, e.g., blacklist.txt",
)
parser.add_argument(
    "--is_sample",
    action="store_true",
    default=False,
    help="Whether to use the sampled dataset",
)
args = parser.parse_args()


# Create log directory if it doesn't exist
if not os.path.exists(os.getenv("LOG_FILE_PATH")):
    os.makedirs(os.getenv("LOG_FILE_PATH"))

# Main log file
logger_cache = {}
main_log_file = os.path.join(
    os.getenv("LOG_FILE_PATH", "./logs"), f"main_{args.start}_{args.end}.log"
)
main_handler = logging.FileHandler(main_log_file, mode="a", encoding="utf-8")
main_handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
main_handler.setFormatter(formatter)

# Main log handler
main_logger = logging.getLogger("main")
main_logger.setLevel(logging.INFO)
main_logger.addHandler(main_handler)


def setup_task_logging(task_id: str = args.q):
    def set_default_logger(logger_name: str, ahandler: logging.Handler = None):
        new_logger = logging.getLogger(logger_name)
        if not hasattr(new_logger, "_handlers_added"):
            new_logger.addHandler(main_handler)
            new_logger.setLevel(logging.INFO)
            new_logger._handlers_added = True
        if task_id and task_id in logger_cache:
            if not any(isinstance(h, logging.FileHandler) for h in new_logger.handlers):
                new_logger.addHandler(logger_cache[task_id].handlers[0])
        if ahandler:
            new_logger.addHandler(ahandler)

    if task_id:
        log_file_name = f"/super_agent_{task_id}.log"
        task_handler = logging.FileHandler(
            os.getenv(
                "LOG_FILE_PATH",
                "run_super_agent.log",
            )
            + log_file_name,
            mode="a",
            encoding="utf-8",
        )
        task_handler.setLevel(logging.INFO)
        task_handler.setFormatter(formatter)

        _task_logger = logging.getLogger(task_id)
        _task_logger.setLevel(logging.INFO)

        if _task_logger.handlers:
            _task_logger.handlers.clear()

        _task_logger.addHandler(task_handler)
        _task_logger.addHandler(main_handler)
        # logging.getLogger("common").addHandler(task_handler)
        # logging.getLogger("root").addHandler(task_handler)
        for name in ["common", "root"]:
            set_default_logger(name, task_handler)

        if task_id in logger_cache:
            return logger_cache[task_id]

        logger_cache[task_id] = _task_logger
        return _task_logger
    return main_logger


if __name__ == "__main__":
    load_dotenv()

    gaia_dataset_path = os.getenv("GAIA_DATASET_PATH", "./gaia_dataset")
    full_dataset = load_dataset_meta(
        gaia_dataset_path, split=args.split, is_sample=args.is_sample
    )
    main_logger.info(f"Total questions: {len(full_dataset)}")

    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name=os.getenv("LLM_MODEL_NAME", "gpt-4o"),
        llm_api_key=os.getenv("LLM_API_KEY", "your_openai_api_key"),
        llm_base_url=os.getenv("LLM_BASE_URL", "your_openai_base_url"),
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
            # "playwright",
            "audio_server",
            "image_server",
            "video_server",
            "search_server",
            "download_server",
            "document_server",
            "browser_server",
            "youtube_server",
            "reasoning_server",
        ],
    )

    # load results from the checkpoint file
    if os.path.exists(os.getenv("LOG_FILE_PATH") + "/results.json"):
        with open(
            os.getenv("LOG_FILE_PATH") + "/results.json", "r", encoding="utf-8"
        ) as results_f:
            results: List[Dict[str, Any]] = json.load(results_f)
    else:
        results: List[Dict[str, Any]] = []

    # load blacklist `task_id`
    if args.blacklist_file_path and os.path.exists(args.blacklist_file_path):
        with open(args.blacklist_file_path, "r", encoding="utf-8") as f:
            blacklist = set(f.read().splitlines())
    else:
        blacklist = set()  # Empty set if file doesn't exist

    try:
        # slice dataset by args.start and args.end, overrided by args.q (single `task_id`)
        dataset_slice = (
            [
                dataset_record
                for idx, dataset_record in enumerate(full_dataset)
                if dataset_record["task_id"] in args.q
            ]
            if args.q is not None
            else full_dataset[args.start : min(args.end, len(full_dataset))]
        )

        # main loop to execute questions
        for i, dataset_i in enumerate(dataset_slice):
            # specify `task_id`
            if args.q and args.q != dataset_i["task_id"]:
                continue
            # only valid for args.q==None
            if not args.q:
                # blacklist
                if dataset_i["task_id"] in blacklist:
                    continue

                # pass
                if any(
                    # Question Done and Correct
                    (result["task_id"] == dataset_i["task_id"] and result["is_correct"])
                    for result in results
                ) or any(
                    # Question Done and Incorrect, but Level is 3
                    (
                        result["task_id"] == dataset_i["task_id"]
                        and not result["is_correct"]
                        and dataset_i["Level"] == 3
                    )
                    for result in results
                ):
                    continue

                # skip
                if args.skip and any(
                    # Question Done and Correct
                    (result["task_id"] == dataset_i["task_id"])
                    for result in results
                ):
                    continue

            # run
            try:
                task_logger = setup_task_logging(task_id=dataset_i["task_id"])

                task_logger.info(f"Start to process: {dataset_i['task_id']}")
                task_logger.info(f"Detail: {dataset_i}")
                task_logger.info(f"Question: {dataset_i['Question']}")
                task_logger.info(f"Level: {dataset_i['Level']}")
                task_logger.info(f"Tools: {dataset_i['Annotator Metadata']['Tools']}")

                question = add_file_path(
                    dataset_i, file_path=gaia_dataset_path, split=args.split
                )["Question"]

                result = Runners.sync_run_task(
                    task=Task(input=question, agent=super_agent, conf=TaskConfig())
                )

                match = re.search(r"<answer>(.*?)</answer>", result["task_0"]["answer"])
                if match:
                    answer = match.group(1)
                    task_logger.info(f"Agent answer: {answer}")
                    task_logger.info(f"Correct answer: {dataset_i['Final answer']}")

                    if question_scorer(answer, dataset_i["Final answer"]):
                        task_logger.info(f"Question {i} Correct!")
                    else:
                        task_logger.info("Incorrect!")

                # Create the new result record
                if args.split == "test":
                    new_result = {
                        "task_id": dataset_i["task_id"],
                        "level": dataset_i["Level"],
                        "question": question,
                        "response": answer,
                    }
                elif args.split == "validation":
                    new_result = {
                        "task_id": dataset_i["task_id"],
                        "level": dataset_i["Level"],
                        "question": question,
                        "answer": dataset_i["Final answer"],
                        "response": answer,
                        "is_correct": question_scorer(
                            answer, dataset_i["Final answer"]
                        ),
                    }
                else:
                    raise ValueError(
                        f"Invalid split: {args.split}."
                        " Must be one of 'validation' or 'test'."
                    )

                # Check if this task_id already exists in results
                existing_index = next(
                    (
                        i
                        for i, result in enumerate(results)
                        if result["task_id"] == dataset_i["task_id"]
                    ),
                    None,
                )

                if existing_index is not None:
                    # Update existing record
                    results[existing_index] = new_result
                    task_logger.info(
                        f"Updated existing record for task_id: {dataset_i['task_id']}"
                    )
                else:
                    # Append new record
                    results.append(new_result)
                    task_logger.info(
                        f"Added new record for task_id: {dataset_i['task_id']}"
                    )

            except Exception as e:
                task_logger.error(f"Error processing {i}: {traceback.format_exc()}")
                continue
    except KeyboardInterrupt:
        pass
    finally:
        # report
        if args.split == "validation":
            report_results(results)

        # write final results to file
        with open(
            os.getenv("LOG_FILE_PATH") + "/results.json", "w", encoding="utf-8"
        ) as f:
            json.dump(results, f, indent=4, ensure_ascii=False)

        # close log handlers
        main_handler.close()
        for logger in logger_cache.values():
            for handler in logger.handlers:
                handler.close()
