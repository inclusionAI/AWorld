# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import json
import os

from aworld.agents.gaia.agent import ExecuteAgent, PlanAgent
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
    model_config = ModelConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_temperature=0.0,
    )

    # Define a task
    save_path = "result.json"
    save_score_path = "score.json"
    if os.path.exists(save_path):
        with open(save_path, "r") as f:
            _results = json.load(f)
    else:
        _results = []
    for idx, sample in enumerate(dataset):
        logger.info(
            f">>> Progress bar: {str(idx)}/{len(dataset)}. Current task {sample['task_id']}. "
        )
        # if sample["task_id"] != "32102e3e-d12a-4209-9163-7b3a104efe5d":
        # continue

        if _check_task_completed(sample["task_id"], _results):
            logger.info(
                f"The following task is already completed:\n task id: {sample['task_id']}, question: {sample['Question']}"
            )
            continue

        question = sample["Question"]
        question = "A paper about AI regulation that was originally submitted to arXiv.org in June 2022 shows a figure with three axes, where each axis has a label word at both ends. Which of these words is used to describe a type of society in a Physics and Society article submitted to arXiv.org on August 11, 2016?"  # 一定要删掉
        logger.info(f"question: {question}")

        # debug
        # question = "What is the surname of the equine veterinarian mentioned in 1.E Exercises from the chemistry materials licensed by Marisa Alviar-Agnew & Henry Agnew under the CK-12 license in LibreText's Introductory Chemistry materials as compiled 08/21/2023?"
        # question = "What is the surname of the horse doctor mentioned in 1.E Exercises from the chemistry materials licensed by Marisa Alviar-Agnew & Henry Agnew under the CK-12 license in LibreText's Introductory Chemistry materials as compiled 08/21/2023?"
        # end debug

        # agent1 = PlanAgent(conf=agent_config)
        # agent2 = ExecuteAgent(conf=agent_config, tool_names=[Tools.DOCUMENT_ANALYSIS.value,
        #                                                     Tools.PYTHON_EXECUTE.value,
        #                                                     Tools.IMAGE_ANALYSIS.value,
        #                                                     Tools.SEARCH_API.value,
        #                                                     Tools.BROWSER.value])
        # agent2 = ExecuteAgent(
        #     conf=agent_config,
        #     tool_names=[
        #         Tools.DOCUMENT_ANALYSIS.value,
        #         Tools.PYTHON_EXECUTE.value,
        #         Tools.IMAGE_ANALYSIS.value,
        #     ],
        # )

        # Create swarm for multi-agents
        # define (head_node1, tail_node1), (head_node1, tail_node1) edge in the topology graph
        # swarm = Swarm((agent1, agent2))

        planner = PlanAgent(
            conf=AgentConfig(name=Agents.PLAN.value, llm_config=model_config)
        )
        executor = ExecuteAgent(
            conf=AgentConfig(name=Agents.EXECUTE.value, llm_config=model_config),
            tool_names=[],
            mcp_servers=[
                "image",
                "audio",
                "video",
                "document",
                "search",
                # "playwright",
            ],
        )

        swarm = Swarm((planner, executor))
        task = Task(input=question, swarm=swarm, conf=TaskConfig())
        result = client.submit(task=[task])

        answer = result["task_0"]["answer"]
        logger.info(f"Task completed: {result['success']}")
        logger.info(f"Time cost: {result['time_cost']}")
        logger.info(f"Task Answer: {answer}")

        # 记录结果
        _result_info = {
            "task_id": sample["task_id"],
            "question": sample["Question"],
            "level": sample["Level"],
            "model_answer": answer,
            "ground_truth": sample["Final answer"],
            "score": question_scorer(answer, sample["Final answer"]),
        }
        _results.append(_result_info)
        # break
        with open(save_path, "w") as f:
            json.dump(_results, f, indent=4, ensure_ascii=False)

        break

    score_dict = _generate_summary(_results)
    print(score_dict)
    with open(save_score_path, "w") as f:
        json.dump(score_dict, f, indent=4, ensure_ascii=False)
