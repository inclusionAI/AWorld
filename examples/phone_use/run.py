# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config import AgentConfig
from examples.phone_use.agent import AndroidAgent
from examples.common.tools.common import Agents, Tools
from aworld.core.task import Task
from aworld.runner import Runners
from examples.common.tools.conf import AndroidToolConfig


def main():
    android_tool_config = AndroidToolConfig(avd_name='8ABX0PHWU',
                                            headless=False,
                                            max_retry=2)

    agent_config: AgentConfig = AgentConfig(
        name=Agents.ANDROID.value,
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_temperature=1,
    )
    agent = AndroidAgent(name=Agents.ANDROID.value, conf=agent_config)

    task = Task(
        input="""open rednote""",
        agent=agent,
        tools_conf={Tools.ANDROID.value, android_tool_config}
    )
    Runners.sync_run_task(task)


if __name__ == '__main__':
    main()
