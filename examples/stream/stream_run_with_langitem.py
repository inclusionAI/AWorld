# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import logging
import os

from dotenv import load_dotenv

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.memory import MemoryConfig, LongTermConfig
from aworld.core.task import Task
from aworld.memory.main import MemoryFactory
from aworld.output import PrinterAworldUI
from aworld.output.ui.base import AworldUI
from aworld.runner import Runners

if __name__ == '__main__':
    application = "stream_run_with_langitem"
    load_dotenv()
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name=os.environ["LLM_MODEL_NAME"],
        llm_api_key=os.environ["LLM_API_KEY"],
        llm_base_url=os.environ["LLM_BASE_URL"]
    )
    application_memory = MemoryFactory.from_config(
        MemoryConfig(
            provider="inmemory",
            enable_long_term=True,
            long_term_config=LongTermConfig.create_simple_config(
                application_id=application,
                enable_user_profiles=False,
                message_threshold=2
            )
        )
    )

    amap_sys_prompt = "You are a helpful agent."
    amap_agent = Agent(
        conf=agent_config,
        name="amap_agent",
        system_prompt=amap_sys_prompt,
        mcp_servers=["filesystem", "amap-amap-sse"],  # MCP server name for agent to use
        history_messages=100,
        memory = application_memory,
        need_reset=False # use long-term must set need_reset = False
    )

    user_input = ("How long does it take to drive from Hangzhou of Zhejiang to  Weihai of Shandong (generate a table with columns for starting point, destination, duration, distance), "
                  "which cities are passed along the way, what interesting places are there along the route, "
                  "and finally generate the content as markdown and save it")

    async def _run(agent, input):
        task = Task(
            user_id="foo",
            session_id="bar",
            input=input,
            agent=agent,
            conf=TaskConfig()
        )

        aworld_ui = PrinterAworldUI()

        async for output in Runners.streamed_run_task(task).stream_events():
            await AworldUI.parse_output(output, aworld_ui)

        logging.info("stream_run_with_langitem end, await 10s")
        await asyncio.sleep(40)

    asyncio.run(_run(amap_agent, user_input))

