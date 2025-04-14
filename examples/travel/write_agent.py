# coding: utf-8

from aworld.agents.travel.prompts import write_prompt, write_sys_prompt, write_output_prompt
from aworld.core.agent.base import Agent
from examples.travel.conf import agent_config

write = Agent(
    conf=agent_config,
    name="example_write_agent",
    system_prompt=write_sys_prompt,
    agent_prompt=write_prompt,
    tool_names=["write_tool"]
)
