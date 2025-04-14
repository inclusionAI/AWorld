# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config.common import Tools
from aworld.core.agent.base import Agent
from examples.travel.conf import agent_config
from examples.travel.prompts import search_sys_prompt, search_prompt, search_output_prompt

# set key and id
# os.environ['GOOGLE_API_KEY'] = ""
# os.environ['GOOGLE_ENGINE_ID'] = ""

search = Agent(
    conf=agent_config,
    name="example_search_agent",
    desc="search ",
    system_prompt=search_sys_prompt,
    agent_prompt=search_prompt,
    tool_names=[Tools.SEARCH_API.value],
)
