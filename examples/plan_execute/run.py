# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.config.common import Agents, Tools
from aworld.config.conf import ModelConfig, AgentConfig, TaskConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.task import Task
from aworld.dataset.mock import mock_dataset
from aworld.runner import Runners
from examples.plan_execute.agent import PlanAgent, ExecuteAgent


def main():
    test_sample = mock_dataset("gaia")

    model_config = ModelConfig(
        llm_provider="openai",
        llm_temperature=1,
        llm_model_name="gpt-4o",
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
    swarm = Swarm((agent1, agent2), sequence=False)

    # Define a task
    task = Task(input=test_sample, swarm=swarm, conf=TaskConfig())

    # Run task
    result = Runners.sync_run_task(task=task)

    print(f"Time cost: {result['time_cost']}")
    print(f"Task Answer: {result['task_0']['answer']}")


if __name__ == '__main__':
    main()
