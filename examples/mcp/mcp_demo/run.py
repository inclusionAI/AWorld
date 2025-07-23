# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners
from examples.mcp.mcp_demo.servers_config import mcp_config

if __name__ == '__main__':
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="YOUR_API_KEY",
        llm_base_url="http://localhost:5080"
    )

    search_sys_prompt = "You can use simple-calculator tools to calculate numbers and answer questions"
    search = Agent(
        conf=agent_config,
        name="search_agent",
        system_prompt=search_sys_prompt,
        # mcp_servers=["amap-amap-sse"],  # MCP server name for agent to use
        mcp_servers = ["simple-calculator"],  # MCP server name for agent to use
        mcp_config=mcp_config
    )

    # Run agent
    Runners.sync_run(input="30,000 divided by 1.2 ", agent=search)