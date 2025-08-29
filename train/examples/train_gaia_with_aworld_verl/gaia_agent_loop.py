# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Union

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm

from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from train.adapter.verl.common import get_agent_tool_env_and_servers

GAIA_SYSTEM_PROMPT = """
You are an all-capable AI assistant, aimed at solving any task presented by the user.
"""

TOOL_ENV_CONFIG = {
    "url": "http://localhost:8000/mcp",
    "authorization": "Bearer dummy",
    "mcp_servers": "readweb-server,browser-server"
}

class GaiaAgentLoop(AworldAgentLoop):
    def build_agents(self, model_name: str = "", base_url: str = "", api_key: str = "") -> Union[Agent, Swarm]:
        # todo: use env.create() to get env_config
        tool_env_config, tool_servers = get_agent_tool_env_and_servers(TOOL_ENV_CONFIG)
        return Agent(
            conf=AgentConfig(
                llm_model_name=model_name,
                llm_base_url=base_url,
                llm_api_key=api_key,
                llm_provider="openai",
            ),
            mcp_config=tool_env_config,
            mcp_servers = tool_servers,
            name="gaia_super_agent",
            system_prompt=GAIA_SYSTEM_PROMPT,
        )
