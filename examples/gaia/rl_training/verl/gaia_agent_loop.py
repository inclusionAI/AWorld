# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Union

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm

from verl.experimental.agent_loop.agent_loop import AgentLoopBase, AgentLoopOutput, AgentLoopMetrics

from examples.gaia.rl_training.verl.aworld_agent_loop import AworldAgentLoop

GAIA_SYSTEM_PROMPT = """
You are an all-capable AI assistant, aimed at solving any task presented by the user.
"""

GAIA_MCP_CONFIG = {
    "mcpServers": {
        "virtualpc-mcp-server": {
            "type": "streamable-http",
            "url": "",
            "headers": {
                "Authorization": "",
                "MCP_SERVERS": "",
            },
            "timeout": 600,
            "sse_read_timeout": 600,
            "client_session_timeout_seconds": 600
        }
    }
}


class GaiaAgentLoop(AworldAgentLoop):
    def build_agent(self) -> Union[Agent, Swarm]:
        return Agent(
            conf=AgentConfig(llm_api_key="dummy"),
            name="gaia_super_agent",
            system_prompt=GAIA_SYSTEM_PROMPT,
            mcp_config=GAIA_MCP_CONFIG,
            mcp_servers=list(server_name for server_name in GAIA_MCP_CONFIG.get("mcpServers", {}).keys()),
        )
