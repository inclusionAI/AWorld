# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os

from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners

if __name__ == '__main__':
    agent_config = AgentConfig(
        llm_provider=os.getenv("LLM_PROVIDER", "openai"),
        llm_model_name=os.getenv("LLM_MODEL_NAME"),
        llm_base_url=os.getenv("LLM_BASE_URL"),
        llm_api_key=os.getenv("LLM_API_KEY"),
        llm_temperature=os.getenv("LLM_TEMPERATURE", 0.0)
    )

    edu_sys_prompt = "You are a helpful agent to convert text to audio for children education."
    edu = Agent(
        conf=agent_config,
        name="edu_agent",
        system_prompt=edu_sys_prompt,
        mcp_servers=["text_to_audio_local_sse"],  # MCP server name for agent to use
        mcp_config={
            "mcpServers": {
                "text_to_audio_local_sse": {
                    "type": "sse",
                    "url": "http://0.0.0.0:8888/sse"
                }
            }
        }
    )

    # run
    Runners.sync_run(
        input="use text_to_audio_local_sse to convert text to audio: Hello, world!",
        agent=edu
    )
