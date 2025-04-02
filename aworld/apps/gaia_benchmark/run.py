# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import json
import os
import logging as logger
from typing import Any, Dict, List, Literal, Optional, Union, Tuple

from aworld.core.client import Client
from aworld.agents.gaia_benchmark.agent import PlanAgent, ExecuteAgent
from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.swarm import Swarm
from aworld.core.task import Task
from aworld.dataset.gaia_benchmark import GAIABenchmark
from aworld.apps.gaia_benchmark.utils import _check_task_completed, question_scorer, _generate_summary
from aworld.core.common import Tools

import os
GOOGLE_API_KEY = ""
GOOGLE_ENGINE_ID = ""
os.environ['GOOGLE_API_KEY'] = GOOGLE_API_KEY
os.environ['GOOGLE_ENGINE_ID'] = GOOGLE_ENGINE_ID


if __name__ == '__main__':
    # Initialize client
    client = Client()

    # One sample for example
    gaia_dir = "~/gaia-benchmark/GAIA"
    dataset = GAIABenchmark(gaia_dir).load()['valid']

    # Create agents
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="",
        llm_base_url="",
    )

    # Define a task
    save_path = '~/result.json'
    save_score_path = '~/score.json'
    if os.path.exists(save_path):
        with open(save_path, 'r') as f:
            _results = json.load(f)
    else:
        _results = []
    for idx, sample in enumerate(dataset):
        logger.info(f">>> Progress bar: {str(idx)}/{len(dataset)}. Current task {sample['task_id']}. ")

        if _check_task_completed(sample["task_id"], _results):
            logger.info(f"The following task is already completed:\n task id: {sample['task_id']}, question: {sample['Question']}")
            continue

        question = sample['Question']
        logger.info(f'question: {question}')

        agent1 = PlanAgent(conf=agent_config)
        agent2 = ExecuteAgent(conf=agent_config, tool_names=[Tools.DOCUMENT_ANALYSIS.value,
                                                            Tools.PYTHON_EXECUTE.value, Tools.SEARCH_API.value])

        # Create swarm for multi-agents
        # define (head_node1, tail_node1), (head_node1, tail_node1) edge in the topology graph
        swarm = Swarm((agent1, agent2))

        task = Task(input=question, swarm=swarm, conf=TaskConfig())

        # Run task
        result = client.submit(task=[task])
        answer = result['task_0']['answer']

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
        with open(save_path, 'w') as f:
            json.dump(_results, f, indent=4, ensure_ascii=False)

    score_dict = _generate_summary(_results)
    print(score_dict)
    with open(save_score_path, 'w') as f:
        json.dump(score_dict, f, indent=4, ensure_ascii=False)

