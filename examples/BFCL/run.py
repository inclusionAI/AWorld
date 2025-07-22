# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners


if __name__ == '__main__':
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="openai/gpt-4o",
        llm_api_key="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",
        llm_base_url="https://openrouter.ai/api/v1"
    )

    # Register the MCP tool here, or create a separate configuration file.
    mcp_config = {
        "mcpServers": {
            "GorillaFileSystem": {
                "type": "sse",
                "url": "http://127.0.0.1:8000/sse/"
            }
        }
    }

    file_sys_prompt = "You are a helpful agent to use the standard file system to perform file operations."
    file_sys = Agent(
        conf=agent_config,
        name="file_sys_agent",
        system_prompt=file_sys_prompt,
        mcp_servers=["GorillaFileSystem"],  # MCP server name for agent to use
        mcp_config=mcp_config
    )

    # run
    Runners.sync_run(
        input="use mcp tools in the GorillaFileSystem server to perform file operations: delete the hello_world.py file if exists, and create a file called hello_world.py with the content 'print('Hello, World!')'    ",
        agent=file_sys
    )