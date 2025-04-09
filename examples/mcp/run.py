# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.task import Task

if __name__ == '__main__':
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="",
        llm_base_url=""
    )

    search_sys_prompt = "You are a helpful agent."
    search = Agent(
        conf=agent_config,
        name="",
        system_prompt=search_sys_prompt,
        mcp_servers=["amap-amap-sse"]
    )

    # Define a task
    task = Task(input="杭州西湖最近1公里的酒店", agent=search, conf=TaskConfig())
    task.run()
