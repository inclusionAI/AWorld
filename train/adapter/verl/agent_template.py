# coding: utf-8
# Copyright (c) 2025 inclusionAI.

VERL_TEMPLATE = """
import uuid
from typing import Union

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ConfigDict
from aworld.core.agent.swarm import Swarm
from aworld.logs.util import logger
from {parser_module} import {parser_name}

from train.adapter.verl.aworld_agent_loop import AworldAgentLoop


class VerlAgentLoop(AworldAgentLoop):
    async def build_agents(self) -> Union[Agent, Swarm]:
        conf = AgentConfig(
            llm_config=ConfigDict(
                llm_model_name=await self.get_llm_server_model_name(),
                llm_base_url=await self.get_llm_server_address(),
                llm_api_key="123",
                llm_provider="verl",
                params={{
                    'client': self.server_manager,
                    "tokenizer": self.tokenizer,
                    "request_id": uuid.uuid4().hex,
                    "tool_parser": "hermes"
                }},
                {kv_parameters}
            ),
        )

        logger.info(f"agent config: ", conf)
        mcp_config = {mcp_config}
        return Agent(
            conf=conf,
            name="{agent_name}",
            desc="{agent_desc}",
            system_prompt="{system_prompt}",
            mcp_config=mcp_config,
            mcp_servers=list(server_name for server_name in mcp_config.get("mcpServers", {{}}).keys()),
            model_output_parser={parser_name}()
        )

"""
