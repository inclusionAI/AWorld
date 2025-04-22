# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import os

from aworld.config.common import Tools
from aworld.config.conf import ModelConfig, AgentConfig
from aworld.core.agent.base import Agent, AgentResult, is_agent_by_name
from aworld.core.common import ActionModel
from examples.travel.prompts import search_sys_prompt, search_prompt, search_output_prompt

# set key and id
# os.environ['GOOGLE_API_KEY'] = ""
# os.environ['GOOGLE_ENGINE_ID'] = ""

model_config = ModelConfig(
    llm_provider="openai",
    llm_model_name="gpt-4o",
    llm_temperature=1,
    # need to set llm_api_key for use LLM
    llm_api_key=""
)
agent_config = AgentConfig(
    llm_config=model_config,
    # use_vision=False
)

search = Agent(
    conf=agent_config,
    name="example_search_agent",
    desc="search ",
    system_prompt=search_sys_prompt,
    agent_prompt=search_prompt,
    # output_prompt=search_output_prompt,
    # resp_parse_func=resp_parse,
    tool_names=[Tools.SEARCH_API.value],
)
