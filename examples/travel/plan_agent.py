# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.config.conf import AgentConfig, ModelConfig

from aworld.core.agent.base import Agent
from examples.travel.prompts import plan_sys_prompt, plan_prompt

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

plan_agent = Agent(
    conf=agent_config,
    name="plan_agent",
    system_prompt=plan_sys_prompt,
    agent_prompt=plan_prompt
)
