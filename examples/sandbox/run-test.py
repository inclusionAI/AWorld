# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config.conf import AgentConfig
from aworld.core.agent.base import Agent
from aworld.runner import Runners
from aworld.sandbox.main import Sandbox

if __name__ == '__main__':
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="sk-",
        llm_base_url="https:"
    )
    mcp_servers = ["tavily-mcp"]
    sand_box = Sandbox(mcp_servers=mcp_servers)

    search_sys_prompt = "You are a versatile assistant"
    search = Agent(
        conf=agent_config,
        name="search_agent",
        system_prompt=search_sys_prompt,
        mcp_servers=mcp_servers,
        sandbox=sand_box,
        #mcp_servers=["amap-amap-sse"],  # MCP server name for agent to use
        #mcp_servers = ["simple-calculator"]  # MCP server name for agent to use
    )

    # Run agent
    Runners.sync_run(input="Use tavily-mcp to check what tourist attractions are in Hangzhou", agent=search)