# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import os
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners
import json


if __name__ == "__main__":
    # Get API key from environment variable
    api_key = os.getenv("OPENROUTER_API_KEY")

    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="openai/gpt-4o",
        llm_api_key=api_key,
        llm_base_url="https://openrouter.ai/api/v1",
    )

    # Register the MCP tool here, or create a separate configuration file.
    mcp_config = {
        "mcpServers": {
            "GorillaFileSystem": {
                "type": "stdio",
                "command": "python",
                "args": ["mcp_tools/gorilla_file_system.py"],
            }
        }
    }

    file_sys_prompt = "You are a helpful agent to use the standard file system to perform file operations."
    file_sys = Agent(
        conf=agent_config,
        name="file_sys_agent",
        system_prompt=file_sys_prompt,
        mcp_servers=mcp_config.get("mcpServers", []).keys(),
        mcp_config=mcp_config,
    )

    # run
    result = Runners.sync_run(
        input=(
            "use mcp tools in the GorillaFileSystem server to perform file operations: "
            "write the content 'AWorld' into the hello_world.py file with a new line "
            "and keep the original content of the file. Make sure the new and old "
            "content are all in the file; and display the content of the file"
        ),
        agent=file_sys,
    )

    print("=" * 100)
    print(f"result.answer: {result.answer}")
    print("=" * 100)
    print(f"result.trajectory: {json.dumps(result.trajectory[0], indent=4)}")
