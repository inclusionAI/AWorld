# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from aworld.agents.llm_agent import Agent
from aworld.core.task import Task
from aworld.core.tool.func_to_tool import be_tool
from aworld.runner import Runners

from examples.aworld_quick_start.common import agent_config

tool_name = "hello_world"


# Function becomes a tool, use `be_tool` decorator
@be_tool(tool_name=tool_name, tool_desc="tool use example")
def hello_world() -> str:
    return "hello world!"


# only used for debug, not used in production
async def main():
    agent = Agent(name="hello_world_agent",
                  conf=agent_config,
                  system_prompt="""You are a helpful agent, and must use hello_world tool as example.""",
                  tool_names=[tool_name])

    task = Task(input="use tool return hello world", agent=agent)
    res = await Runners.run_task(
        task=task
    )
    print(res.get(task.id).answer)


if __name__ == "__main__":
    asyncio.run(main())
