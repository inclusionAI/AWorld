# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os

from aworld.core.runtime_engine import RAY, SPARK, LOCAL

os.environ['RAY_PICKLE_VERBOSE_DEBUG'] = '2'
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.task import Task
from aworld.runner import Runners
from aworld.runners.event_runner import TaskEventRunner
from examples.tools.common import Tools, Agents
from examples.gym_demo.agent import GymDemoAgent as GymAgent
from examples.tools.gym_tool.async_openai_gym import OpenAIGym


def main():
    agent = GymAgent(name=Agents.GYM.value, conf=AgentConfig(), tool_names=[Tools.GYM.value])
    gym_tool = OpenAIGym(name=Tools.GYM.value,
                         conf={"env_id": "CartPole-v1", "render_mode": "human", "render": True})

    # It can also be used `ToolFactory` for simplification.
    # gym_tool = ToolFactory(Tools.GYM.value)
    task = Task(agent=agent, tools=[gym_tool])
    res = Runners.sync_run_task(task=task, run_conf=ConfigDict({"name": LOCAL}))
    # # ex = Runners.exec_task(task, ConfigDict({"name": RAY}))
    #
    # run_conf = ConfigDict({"name": "ray"})
    # name = run_conf.name
    # if run_conf.get('cls'):
    #     runtime_backend = new_instance(run_conf.cls, run_conf)
    # else:
    #     runtime_backend = new_instance(
    #         f"aworld.core.runtime_engine.{snake_to_camel(name)}Runtime", run_conf)
    # runtime_engine = runtime_backend.build_engine()
    # return sync_exec(runtime_engine.execute, Runners.exec_task, [task])
    # from ray.util import inspect_serializability
    # inspect_serializability(runner, name="test")
    return res


if __name__ == "__main__":
    # We use it as a showcase to demonstrate the framework's scalability.
    main()
