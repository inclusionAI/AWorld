# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import json
import os
from aworld.agents.browser.agent import BrowserAgent
from aworld.agents.browser.config import BrowserAgentConfig
from aworld.core.envs.tool import ToolFactory
from aworld.logs.util import logger
from typing import Any, Dict, List, Literal, Optional, Union, Tuple

from aworld.core.client import Client
from aworld.virtual_environments.conf import BrowserToolConfig
from aworld.agents.gaia_benchmark.agent import PlanAgent, ExecuteAgent
from aworld.config.conf import AgentConfig, ModelConfig, TaskConfig
from aworld.core.swarm import Swarm
from aworld.core.task import Task
from aworld.dataset.gaia_benchmark import GAIABenchmark
from aworld.apps.gaia_benchmark.utils import _check_task_completed, question_scorer, _generate_summary
from aworld.core.common import Agents, Tools

import os
# GOOGLE_API_KEY = ""
# GOOGLE_ENGINE_ID = ""
GOOGLE_API_KEY="AIzaSyBz68rKBQNmUV-0zM8KMqiK6qrhF-JuK_k" ## zhuige
GOOGLE_ENGINE_ID="c790a773fba27404b"
os.environ['GOOGLE_API_KEY'] = GOOGLE_API_KEY
os.environ['GOOGLE_ENGINE_ID'] = GOOGLE_ENGINE_ID


if __name__ == '__main__':
    # Initialize client
    client = Client()

    # One sample for example
    gaia_dir = "/Users/zhuige/Documents/llm/agent/projects/web_understanding/datasets/GAIA"
    dataset = GAIABenchmark(gaia_dir).load()['valid']

    # Create agents
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="dummy-key",
        llm_base_url="http://localhost:5000"
    )

    # Define a task
    save_path = 'result.json'
    save_score_path = 'score.json'
    if os.path.exists(save_path):
        with open(save_path, 'r') as f:
            _results = json.load(f)
    else:
        _results = []
    for idx, sample in enumerate(dataset):
        logger.info(f">>> Progress bar: {str(idx)}/{len(dataset)}. Current task {sample['task_id']}. ")
        # if sample["task_id"] != "df6561b2-7ee5-4540-baab-5095f742716a":
            # continue
        if sample["task_id"] != "5a0c1adf-205e-4841-a666-7c3ef95def9d":
            continue

        if _check_task_completed(sample["task_id"], _results):
            logger.info(f"The following task is already completed:\n task id: {sample['task_id']}, question: {sample['Question']}")
            continue

        question = sample['Question']
        logger.info(f'question: {question}')

        # debug
        question = "打开wikipedia"
        # question = "What is the surname of the horse doctor mentioned in 1.E Exercises from the chemistry materials licensed by Marisa Alviar-Agnew & Henry Agnew under the CK-12 license in LibreText's Introductory Chemistry materials as compiled 08/21/2023?"
        # end debug

        inner_llm_model_config = ModelConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_temperature=0.3,
            llm_api_key="dummy-key",
            llm_base_url="http://localhost:5000",
            max_input_tokens = 128000
        )
        browser_tool_config = BrowserToolConfig(width=1280,
                                                height=720,
                                                headless=False,
                                                keep_browser_open=True,
                                                llm_config=inner_llm_model_config)
        browser_agent_config = BrowserAgentConfig(
            tool_calling_method="raw",
            agent_name=Agents.BROWSER.value,
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_num_ctx=32000,
            llm_temperature=1,
            llm_api_key="dummy-key",
            llm_base_url="http://localhost:5000",
            max_actions_per_step=10
        )

        # agent_config = AgentConfig(
        #     agent_name=Agents.BROWSER.value,
        #     llm_provider="chatopenai",
        #     llm_model_name="gpt-4o-mini",
        #     llm_num_ctx=32000,
        #     llm_temperature=1,
        #     max_actions_per_step=10,
        #     max_steps=100,
        # )
        browser_agent=BrowserAgent(conf=browser_agent_config)

        agent1 = PlanAgent(conf=agent_config)
        # agent2 = ExecuteAgent(conf=agent_config, tool_names=[Tools.DOCUMENT_ANALYSIS.value,
        #                                                     Tools.PYTHON_EXECUTE.value, 
        #                                                     Tools.IMAGE_ANALYSIS.value,
        #                                                     Tools.SEARCH_API.value,
        #                                                     Tools.BROWSER.value])
        agent2 = ExecuteAgent(conf=agent_config, tool_names=[Tools.DOCUMENT_ANALYSIS.value,
                                                            Tools.PYTHON_EXECUTE.value, 
                                                            Tools.IMAGE_ANALYSIS.value
                                                            ])
                                                            # ,agent_names=[browser_agent])

        # Create swarm for multi-agents
        # define (head_node1, tail_node1), (head_node1, tail_node1) edge in the topology graph
        swarm = Swarm((agent1, agent2), (agent2, browser_agent))

        task = Task(input=question, swarm=swarm, conf=TaskConfig(),tools=[ToolFactory(Tools.BROWSER.value, conf=browser_tool_config)])

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
        logger.info(_result_info, indent=4, ensure_ascii=False)
        break
        with open(save_path, 'w') as f:
            json.dump(_results, f, indent=4, ensure_ascii=False)

    score_dict = _generate_summary(_results)
    print(score_dict)
    with open(save_score_path, 'w') as f:
        json.dump(score_dict, f, indent=4, ensure_ascii=False)