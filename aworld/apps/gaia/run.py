# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import json
import os

from aworld.agents.gaia.agent import ExecuteAgent, PlanAgent
from aworld.agents.gaia.gaia_agent import GaiaAgents, GaiaExecuteAgent, GaiaPlanAgent
from aworld.apps.gaia.utils import (
    _check_task_completed,
    _generate_summary,
    question_scorer,
)
from aworld.config.common import Agents, Tools
from aworld.config.conf import AgentConfig, ModelConfig, TaskConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.client import Client
from aworld.core.task import Task
from aworld.dataset.gaia.benchmark import GAIABenchmark
from aworld.logs.util import logger

if __name__ == "__main__":
    # Initialize client
    client = Client()

    # One sample for example
    gaia_dir = os.path.expanduser("~/gaia-benchmark/GAIA")
    dataset = GAIABenchmark(gaia_dir).load()["valid"]

    # Create agents
    llm_api_key = os.getenv("LLM_API_KEY", "")
    llm_base_url = os.getenv("LLM_BASE_URL", "")
    logger.success(
        f"\n>>> llm_api_key: {llm_api_key}\n>>> llm_base_url: {llm_base_url}"
    )

    # Define a task
    save_path = "/Users/arac/Desktop/gaia/result.json"
    save_score_path = "/Users/arac/Desktop/gaia/score.json"
    if os.path.exists(save_path):
        with open(save_path, "r") as f:
            _results = json.load(f)
    else:
        _results = []
    for idx, sample in enumerate(dataset):
        logger.info(
            f">>> Progress bar: {str(idx)}/{len(dataset)}. Current task {sample['task_id']}. "
        )

        if _check_task_completed(sample["task_id"], _results):
            logger.info(
                f"The following task is already completed:\n task id: {sample['task_id']}, question: {sample['Question']}"
            )
            continue

        question = sample["Question"]
        logger.info(f"question: {question}")

        planner = PlanAgent(
            conf=AgentConfig(
                name=Agents.PLAN.value,
                llm_provider="openai",
                llm_model_name="gpt-4o",
                llm_base_url="http://localhost:3456",
                llm_api_key="dummy-key",
                # llm_api_key=llm_api_key,
                # llm_base_url=llm_base_url,
                llm_temperature=0.1,
            )
        )
        executor = ExecuteAgent(
            conf=AgentConfig(
                name=Agents.EXECUTE.value,
                llm_provider="openai",
                llm_model_name="gpt-4o",
                llm_base_url="http://localhost:3456",
                llm_api_key="dummy-key",
                # llm_base_url=llm_base_url,D
                # llm_api_key=llm_api_key,
                llm_temperature=0.1,
            ),
            tool_names=[],
            mcp_servers=[
                "arxiv",
                "audio",
                "code",
                "document",
                "download",
                "filesystem",
                "github",
                "googlemaps",
                "image",
                "math",
                "reddit",
                "search",
                # "sympy",
                "video",
                "playwright",
                "wikipedia",
                "orcid",
            ],
        )

        swarm = Swarm((planner, executor), sequence=False)
        task = Task(input=question, swarm=swarm, conf=TaskConfig())
        result = client.submit(task=[task])

        answer = result["task_0"]["answer"]
        logger.info(f"Task completed: {result['success']}")
        logger.info(f"Time cost: {result['time_cost']}")
        logger.info(f"Task Answer: {answer}")

        _result_info = {
            "task_id": sample["task_id"],
            "question": sample["Question"],
            "level": sample["Level"],
            "model_answer": answer,
            "ground_truth": sample["Final answer"],
            "score": question_scorer(answer, sample["Final answer"]),
        }
        _results.append(_result_info)
        with open(save_path, "w") as f:
            json.dump(_results, f, indent=4, ensure_ascii=False)

    score_dict = _generate_summary(_results)
    print(score_dict)
    with open(save_score_path, "w") as f:
        json.dump(score_dict, f, indent=4, ensure_ascii=False)
