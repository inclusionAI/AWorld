# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Union

from train.examples.train_gaia_with_aworld_verl.gaia import build_mcp_config

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
# from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from train.adapter.verl.agent_loop import AworldAgentLoop
from train.examples.train_gaia_with_aworld_verl.gaia import build_gaia_agent


class GaiaAgentLoop(AworldAgentLoop):
    async def build_agents(self) -> Union[Agent, Swarm]:
        # gaia_env_config, gaia_env_servers = get_agent_tool_env_and_servers()

        print(f"######## self.get_llm_server_model_name(): {await self.get_llm_server_model_name()} ########",flush=True)
        print(f"######## self.get_llm_server_address(): {await self.get_llm_server_address()} ########",flush=True)

        return build_gaia_agent(
            llm_model_name=await self.get_llm_server_model_name(),
            llm_base_url=await self.get_llm_server_address(),
            llm_api_key="123",
            mcp_config=build_mcp_config(),
            server_manager=self.server_manager,
            tokenizer=self.tokenizer
        )

