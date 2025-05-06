# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config.common import Tools, Agents
from aworld.agents.gym.agent import GymDemoAgent as GymAgent

from aworld.config.conf import AgentConfig
from aworld.core.task import Task
from aworld.logs.util import trace_logger, Color
from aworld.runner import Runners
from aworld.virtual_environments.gym_tool.async_openai_gym import OpenAIGym


def main():
    agent = GymAgent(name=Agents.GYM.value, conf=AgentConfig(), tool_names=[Tools.GYM.value])
    gym_tool = OpenAIGym(name=Tools.GYM.value,
                         conf={"env_id": "CartPole-v1", "render_mode": "human", "render": True})

    # It can also be used `ToolFactory` for simplification.
    # gym_tool = ToolFactory(Tools.GYM.value)
    task = Task(agent=agent, tools=[gym_tool])
    res = Runners.sync_run_task(task=task)
    return res


if __name__ == "__main__":
    # We use it as a showcase to demonstrate the framework's scalability.
    main()
