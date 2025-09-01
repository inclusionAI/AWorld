# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Union

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm

from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from train.adapter.verl.common import get_agent_tool_env_and_servers
from train.train_env import TranEnv

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
        # # todo: use env.create() to get env_config
        # tool_env_config, tool_servers = get_agent_tool_env_and_servers(TOOL_ENV_CONFIG)
        train_env = TranEnv()
        env_service = train_env.create_env(name="GAIA", mode="local")
        if not env_service:
            raise ValueError("Env is not ready!")

        return Agent(
            conf=AgentConfig(
                llm_model_name=model_name,
                llm_base_url=base_url,
                llm_api_key=api_key,
                llm_provider="openai",
            ),
            name="gaia_super_agent",
            system_prompt=GAIA_SYSTEM_PROMPT,

            # MCP tool configuration for the agent
            mcp_config=train_env.get_env_config().get("mcp_config"),
            mcp_servers=train_env.get_env_config().get("mcp_servers"),
        )
