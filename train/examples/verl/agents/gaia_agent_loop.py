# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Union

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm

from train.frameworks.verl.aworld_agent_loop import AworldAgentLoop

GAIA_SYSTEM_PROMPT = """
You are an all-capable AI assistant, aimed at solving any task presented by the user.
"""

class GaiaAgentLoop(AworldAgentLoop):
    def build_agents(self, model_name: str = "", base_url: str = "", api_key: str = "") -> Union[Agent, Swarm]:
        return Agent(
            conf=AgentConfig(
                llm_model_name=model_name,
                llm_base_url=base_url,
                llm_api_key=api_key,
                llm_provider="openai",
            ),
            name="gaia_super_agent",
            system_prompt=GAIA_SYSTEM_PROMPT,
        )
