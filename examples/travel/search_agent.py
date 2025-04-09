# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os

from aworld.config.common import Tools
from aworld.config.conf import ModelConfig, AgentConfig
from aworld.core.agent.base import Agent
from examples.travel.prompts import search_sys_prompt, search_prompt, search_output_prompt

# set key and id
os.environ['GOOGLE_API_KEY'] = ""
os.environ['GOOGLE_ENGINE_ID'] = ""

model_config = ModelConfig(
    llm_provider="openai",
    llm_model_name="gpt-4o",
    llm_temperature=1,
    # need to set llm_api_key for use LLM
    llm_api_key=""
)
agent_config = AgentConfig(
    llm_config=model_config,
)

search = Agent(
    conf=agent_config,
    name="search_agent",
    system_prompt=search_sys_prompt,
    agent_prompt=search_prompt,
    output_prompt=search_output_prompt,
    tool_names=[Tools.SEARCH_API.value]
)
