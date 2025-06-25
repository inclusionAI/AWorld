# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config.conf import ModelConfig, AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.task import Task
from aworld.runner import Runners
from examples.plan_execute.agent import PlanAgent, ExecuteAgent
from examples.plan_execute.mock import mock_dataset
from examples.tools.common import Agents, Tools


def main():
    test_sample = mock_dataset("gaia")

    model_config = ModelConfig(
        llm_provider="openai",
        llm_temperature=1,
        llm_model_name="gpt-4o",
        # need to set llm_api_key for use LLM
    )

    agent1_config = AgentConfig(
        name=Agents.PLAN.value,
        llm_config=model_config
    )
    agent1 = PlanAgent(conf=agent1_config, step_reset=False)

    agent2_config = AgentConfig(
        name=Agents.EXECUTE.value,
        llm_config=model_config
    )
    agent2 = ExecuteAgent(conf=agent2_config, step_reset=False, tool_names=[Tools.DOCUMENT_ANALYSIS.value])

    # Create swarm for multi-agents
    # define (head_node1, tail_node1), (head_node1, tail_node1) edge in the topology graph
    swarm = Swarm((agent1, agent2), workflow=False)

    # Define a task
    task_id = 'task'
    task = Task(id=task_id, input=test_sample, swarm=swarm, endless_threshold=10)

    # Run task
    result = Runners.sync_run_task(task=task)

    print(f"Time cost: {result[task_id].time_cost}")
    print(f"Task Answer: {result[task_id].answer}")


if __name__ == '__main__':
    main()
