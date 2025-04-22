# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.task import Task

if __name__ == '__main__':
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="YOUR_API_KEY",
        llm_base_url="http://localhost:5080"
    )

    search_sys_prompt = "You are a helpful agent."
    search = Agent(
        conf=agent_config,
        name="search_agent",
        system_prompt=search_sys_prompt,
        mcp_servers=["amap-amap-sse"]  # MCP server name for agent to use
    )

    # Define a task
    Task(
        input="Hotels within 1 kilometer of West Lake in Hangzhou", agent=search, conf=TaskConfig()
    ).run()
