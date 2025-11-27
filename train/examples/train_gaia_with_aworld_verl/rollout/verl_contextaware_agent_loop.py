import os
from typing import Union, Any

from aworld.agents.llm_agent import Agent
from aworld.config import TaskConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.base import Context
from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from train.examples.train_gaia_with_aworld_verl.mcp_tools import build_mcp_config
from train.examples.train_gaia_with_aworld_verl.rollout.agent_config import build_context_config, \
    build_context_aware_task_config, build_context_aware_agent


class VerlAgentLoop(AworldAgentLoop):
    async def build_context(self, input: Any) -> Context:
        return await ApplicationContext.from_input(task_input=input,
                                                   context_config=build_context_config())

    async def build_task_config(self) -> TaskConfig:
        return build_context_aware_task_config()

    async def build_agents(self) -> Union[Agent, Swarm]:
        return build_context_aware_agent(llm_model_name=await self.get_llm_server_model_name(),
                                         llm_base_url=await self.get_llm_server_address(),
                                         # TODO use template env variables
                                         llm_api_key=os.environ['LLM_API_KEY'],
                                         llm_provider="verl",
                                         mcp_config=await build_mcp_config(),
                                         server_manager=self.server_manager,
                                         tokenizer=self.tokenizer)
