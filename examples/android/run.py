# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.agents import AndroidAgent
from aworld.config import AgentConfig
from aworld.core.agent.base import AgentFactory
from aworld.core.client import Client
from aworld.core.common import Agents, Tools
from aworld.core.envs.tool import ToolFactory
from aworld.core.task import Task
from aworld.virtual_environments.conf import AndroidToolConfig


def main():
    client = Client()
    android_tool_config = AndroidToolConfig(avd_name='8ABX0PHWU',
                                            headless=False,
                                            max_retry=2)

    agent_config: AgentConfig = AgentConfig(
        name=Agents.ANDROID.value,
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_temperature=1,
    )

    task_config = {
        'max_steps': 100,
        'max_actions_per_step': 100
    }
    client.submit(Task(input="""open rednote""",
                       agent=AgentFactory(Agents.ANDROID.value, conf=agent_config),
                       tools=[ToolFactory(Tools.ANDROID.value, conf=android_tool_config)],
                       task_config=task_config))


if __name__ == '__main__':
    main()
