import json
import logging
import os
import re
import traceback
from typing import Any, Dict, List, Set

from dotenv import load_dotenv

from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.task import Task
from aworld.runner import Runners
from examples.gaia.prompt import system_prompt
from examples.gaia.utils import (
    add_file_path,
    load_dataset_meta,
    parse_arguments,
    question_scorer,
    report_results,
)

# Load environment variables from .env file
load_dotenv()
# Get command line arguments
args = parse_arguments()

# Create log directory if it doesn't exist
log_path = os.getenv("LOG_FILE_PATH")
os.makedirs(log_path, exist_ok=True)

# Logger configuration
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger_cache = {}

# Global loggers defined by AWorld framework
common_logger = logging.getLogger("common")
root_logger = logging.getLogger("root")


def setup_main_logging():
    # Main log file
    main_log_file = os.path.join(log_path, f"main_{args.start}_{args.end}.log")
    main_handler = logging.FileHandler(main_log_file, mode="a", encoding="utf-8")
    main_handler.setLevel(logging.INFO)
    main_handler.setFormatter(formatter)

    # Main log handler
    _main_logger = logging.getLogger("main")
    _main_logger.setLevel(logging.INFO)
    _main_logger.addHandler(main_handler)

    # Direct common and root logs to main log file
    common_logger.addHandler(main_handler)
    root_logger.addHandler(main_handler)
    return _main_logger


def setup_task_logging(task_id: str = args.q):  # pylint: disable=W0621
    def set_default_logger(logger_name: str, ahandler: logging.Handler = None):
        if ahandler:
            new_logger = logging.getLogger(logger_name)
            new_logger.addHandler(ahandler)

    if task_id:
        # If initialized, return the logger from the cache
        if task_id in logger_cache:
            return logger_cache[task_id]

        # Handler
        task_handler = logging.FileHandler(
            log_path + f"/super_agent_{task_id}.log",
            mode="a",
            encoding="utf-8",
        )
        task_handler.setLevel(logging.INFO)
        task_handler.setFormatter(formatter)

        # Logger
        _task_logger = logging.getLogger(task_id)
        _task_logger.setLevel(logging.INFO)

        _task_logger.addHandler(task_handler)
        for name in ["common", "root", "main"]:
            set_default_logger(name, task_handler)

        logger_cache[task_id] = _task_logger
        return _task_logger
    return logging.getLogger("main")


if __name__ == "__main__":
    main_logger = setup_main_logging()

    gaia_dataset_path: str = os.getenv("GAIA_DATASET_PATH", "./gaia_dataset")
    full_dataset: List[Dict[str, Any]] = load_dataset_meta(
        gaia_dataset_path, split=args.split, is_sample=args.is_sample
    )

    agent_config: AgentConfig = AgentConfig(
        llm_provider="openai",
        llm_model_name=os.getenv("LLM_MODEL_NAME", "gpt-4o"),
        llm_api_key=os.getenv("LLM_API_KEY", "your_openai_api_key"),
        llm_base_url=os.getenv("LLM_BASE_URL", "your_openai_base_url"),
    )
    super_agent: Agent = Agent(
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
            "pdf_server",
            "browser_server",
            "youtube_server",
            "reasoning_server",
            "wikipedia_server",
        ],
    )

    # load results from the checkpoint file
    if os.path.exists(log_path + "/results.json"):
        with open(log_path + "/results.json", "r", encoding="utf-8") as results_f:
            results: List[Dict[str, Any]] = json.load(results_f)
    else:
        results: List[Dict[str, Any]] = []

    # load blacklist `task_id`
    if args.blacklist_file_path and os.path.exists(args.blacklist_file_path):
        with open(args.blacklist_file_path, "r", encoding="utf-8") as f:
            blacklist: Set[str] = set(f.read().splitlines())
    else:
        blacklist: Set[str] = set()  # Empty set if file doesn't exist

    try:
        # slice dataset by args.start and args.end, overrided by args.q (single `task_id`)
        dataset_slice: List[Dict[str, Any]] = (
            [
                dataset_record
                for idx, dataset_record in enumerate(full_dataset)
                if dataset_record["task_id"] in args.q
            ]
            if args.q is not None
            else full_dataset[args.start : min(args.end, len(full_dataset))]
        )
        main_logger.info(
            (
                f">>> # total questions: {len(full_dataset)}\n"
                f">>> # sliced questions: {len(dataset_slice)}"
            )
        )

        # main loop to execute questions
        for i, dataset_i in enumerate(dataset_slice):
            task_id = dataset_i["task_id"]

            # specify `task_id`
            if args.q and args.q != task_id:
                continue
            # only valid for args.q==None
            if not args.q:
                # blacklist
                if task_id in blacklist:
                    continue

                # pass
                if any(
                    # Question Done and Correct
                    # Only work for validation split
                    (
                        result["task_id"] == task_id
                        and args.split == "validation"
                        and result["is_correct"]
                    )
                    for result in results
                ) or any(
                    # Question Done and Incorrect, but Level is 3
                    (
                        result["task_id"] == task_id
                        and args.split == "validation"
                        and not result["is_correct"]
                        and dataset_i["Level"] == 3
                    )
                    for result in results
                ):
                    continue

                # skip
                if args.skip and any(
                    # Question Done and Correct
                    (result["task_id"] == task_id and result["answer"] != "<FAILED/>")
                    or (
                        result["task_id"] == task_id
                        and result["answer"] == "<FAILED/>"
                        and result["level"] == 3
                    )
                    for result in results
                ):
                    continue

            # run
            try:
                task_logger = setup_task_logging(task_id=task_id)

                task_logger.info(f"Start to process: {task_id}")
                task_logger.info(f"Detail: {dataset_i}")
                task_logger.info(f"Question: {dataset_i['Question']}")
                task_logger.info(f"Level: {dataset_i['Level']}")

                question: str = add_file_path(
                    dataset_i, file_path=gaia_dataset_path, split=args.split
                )["Question"]

                result: Dict[str, Dict[str, Any]] = Runners.sync_run_task(
                    task=Task(input=question, agent=super_agent, conf=TaskConfig())
                )

                match: bool = re.search(
                    r"<answer>(.*?)</answer>", result["task_0"]["answer"]
                )

                if match:
                    answer: str = match.group(1)
                    task_logger.info(f"Agent answer: {answer}")
                    task_logger.info(f"Correct answer: {dataset_i['Final answer']}")

                    if question_scorer(answer, dataset_i["Final answer"]):
                        task_logger.info(f"Question {task_id} Correct!")
                    else:
                        task_logger.info(f"Question {task_id} Incorrect!")

                # Create the new result record
                if args.split == "test":
                    new_result = {
                        "task_id": task_id,
                        "level": dataset_i["Level"],
                        "question": question,
                        "answer": answer or "",
                    }
                elif args.split == "validation":
                    new_result = {
                        "task_id": task_id,
                        "level": dataset_i["Level"],
                        "question": question,
                        "answer": dataset_i["Final answer"],
                        "response": answer or "",
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
                        if result["task_id"] == task_id
                    ),
                    None,
                )
                if existing_index is not None:
                    # Update existing record
                    results[existing_index] = new_result
                    task_logger.info(f"Updated existing record for task_id: {task_id}")
                else:
                    # Append new record
                    results.append(new_result)
                    task_logger.info(f"Added new record for task_id: {task_id}")
            except Exception as e:
                task_logger.error(f"Error processing {i}: {traceback.format_exc()}")
                continue
            finally:
                target_handler = task_logger.handlers[0]
                # close log handlers
                main_logger.removeHandler(target_handler)
                common_logger.removeHandler(target_handler)
                root_logger.removeHandler(target_handler)

                for logger_handler in task_logger.handlers:
                    logger_handler.close()
                logger_cache.pop(task_id, None)
    except KeyboardInterrupt:
        pass
    finally:
        # report
        if args.split == "validation":
            report_results(results)

        # write final results to file
        with open(log_path + "/results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4, ensure_ascii=False)

        # close log handlers
        for main_hanlder in main_logger.handlers:
            main_hanlder.close()
        for logger in logger_cache.values():
            for logger_handler in logger.handlers:
                logger_handler.close()
