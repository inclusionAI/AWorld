# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import os

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))) 

from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.agents.bfcl_agent import BFCLAgent
from aworld.runner import Runners
import json


if __name__ == "__main__":
    # Get API key from environment variable
    api_key = os.getenv("OPENROUTER_API_KEY")

    agent_config = AgentConfig(
        llm_provider="Google",
        llm_model_name="google/gemini-2.0-flash-lite-001",
        llm_api_key=api_key,
        llm_base_url="https://openrouter.ai/api/v1",
        llm_temperature=0.001,
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

    file_sys_prompt = "You are a helpful agent."

    
    # file_sys = Agent(
    #     conf=agent_config,
    #     name="file_sys_agent",
    #     system_prompt=file_sys_prompt,
    #     mcp_servers=mcp_config.get("mcpServers", []).keys(),
    #     mcp_config=mcp_config,
    # )

    # # run
    # result = Runners.sync_run(
    #     input=(
    #         "use mcp tools in the GorillaFileSystem server to perform file operations: "
    #         "write the content 'AWorld' into the hello_world.py file with a new line "
    #         "and keep the original content of the file. Make sure the new and old "
    #         "content are all in the file; and display the content of the file"
    #     ),
    #     agent=file_sys,
    # )

    file_sys = BFCLAgent(
        conf=agent_config,
        name="file_sys_agent",
        system_prompt=file_sys_prompt,
        mcp_servers=mcp_config.get("mcpServers", []).keys(),
        mcp_config=mcp_config,
    )

    result = Runners.sync_run(
        input=[
            {"role": "system", "content": "You are a helpful agent to use the standard file system to perform file operations."},
            {"role": "user", "content": "use mcp tools in the GorillaFileSystem server to perform file operations: "}
        ],
        agent=file_sys,
    )

    print("=" * 100)
    print(f"result.answer: {result.answer}")
    print("=" * 100)
    print(f"result.trajectory: {json.dumps(result.trajectory[0], indent=4)}")
