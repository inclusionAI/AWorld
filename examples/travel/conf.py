# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.agents.browser.config import BrowserAgentConfig
from aworld.config.conf import ModelConfig, AgentConfig

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

browser_agent_config = BrowserAgentConfig(
    llm_config=model_config,
    # use_vision=False
)
