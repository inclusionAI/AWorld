# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import os

from aworld.runner import Runners

from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.task import Task
from aworld.output.ui.base import AworldUI
from custom.custom_rich_aworld_ui import RichAworldUI

if __name__ == '__main__':

    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name=os.environ["LLM_MODEL_NAME"],
        llm_api_key=os.environ["LLM_API_KEY"],
        llm_base_url=os.environ["LLM_BASE_URL"]
    )

    amap_sys_prompt = "You are a helpful agent."
    amap_agent = Agent(
        conf=agent_config,
        name="amap_agent",
        system_prompt=amap_sys_prompt,
        mcp_servers=["filesystem", "amap-amap-sse"],  # MCP server name for agent to use
        history_messages=100
    )

    user_input = ("How long does it take to drive from Hangzhou of Zhejiang to  Weihai of Shandong (generate a table with columns for starting point, destination, duration, distance), "
                  "which cities are passed along the way, what interesting places are there along the route, "
                  "and finally generate the content as markdown and save it")


    async def _run(agent, input):
        task = Task(
            input=input,
            agent=agent,
            conf=TaskConfig()
        )

        rich_ui = RichAworldUI()

        async for output in Runners.streamed_run_task(task).stream_events():
            await AworldUI.parse_output(output, rich_ui)


    asyncio.run(_run(amap_agent, user_input))
