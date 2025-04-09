# coding: utf-8

from aworld.agents.travel.prompts import write_prompt, write_sys_prompt, write_output_prompt
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.agent.base import Agent

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
    system_prompt=write_sys_prompt,
    agent_prompt=write_prompt,
    output_prompt=write_output_prompt,
    tool_names=["write_tool"]
)
