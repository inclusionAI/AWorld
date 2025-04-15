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

    edu_sys_prompt = "You are a helpful agent to convert text to audio for children education."
    edu = Agent(
        conf=agent_config,
        name="edu_agent",
        system_prompt=edu_sys_prompt,
        mcp_servers=["text_to_audio_local_sse"]  # MCP server name for agent to use
    )

    # Define a task
    Task(
        input="use text_to_audio_local_sse to convert text to audio: Hello, world!", agent=edu, conf=TaskConfig()
    ).run()
