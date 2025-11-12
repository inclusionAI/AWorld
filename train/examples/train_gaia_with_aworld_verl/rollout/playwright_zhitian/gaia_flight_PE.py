# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import logging
import os
from typing import Optional

from pydantic import BaseModel

from amnicontext import ApplicationContext, TaskInput
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ModelConfig, TaskConfig
from aworld.core.task import Task
from aworld.tools.human.human import HUMAN
# from aworldspace.agents.gaia_agent.gaia_agent import GaiaAgent
# from aworldspace.agents.gaia_agent.gaia_mcp import gaia_mcp_servers, gaia_mcp_config
# from aworldspace.agents.gaia_agent.prompt.gaia_prompt import gaia_agent_system_prompt
from flight_plan_agent import FlightPlanAgent
from executor_agent_shell import GaiaPlayWrightAgent
from mcp.gaia_playwright_mcp_config import gaia_playwright_mcp_config
from mcp.gaia_playwright_mcp_servers import gaia_playwright_mcp_servers
from prompt.flight_plan_prompt import get_flight_plan_agent_system_prompt
from prompt.gaia_playwright_prompt import get_gaia_playwright_agent_system_prompt
from aworldspace.base_agent import AworldBasePipeline, CustomMarkdownAworldUI
# from aworldspace.data.gaia_utils import load_dataset_meta, add_file_path
from aworldspace.utils.model_config import get_model_config
from aworld.config import AgentConfig, ModelConfig, AgentMemoryConfig
from aworld.core.agent.swarm import Swarm, TeamSwarm


class Pipeline(AworldBasePipeline):
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        logging.info("gaia_playwright_agent init success")

    # async def _build_agent(self, context: ApplicationContext) -> Optional[Agent]:
    async def _build_swarm(self, context: ApplicationContext) -> Optional[Swarm]:
        # agent_config = AgentConfig(
        #     llm_config=ModelConfig(
        #         **get_model_config(os.environ["GAIA_AGENT_LLM_MODEL_NAME"])
        #     ),
        #     memory_config=AgentMemoryConfig(history_rounds=30),
        #     use_vision=False
        # )
        agent_config_plan = AgentConfig(
            llm_config=ModelConfig(
                **get_model_config(os.environ["FLIGHT_PE_AGENT_LLM_MODEL_NAME"])
            ),
            # memory_config=AgentMemoryConfig(history_rounds=4),
            use_vision=False
        )

        agent_config_execute = AgentConfig(
            llm_config=ModelConfig(
                **get_model_config(os.environ["FLIGHT_PE_AGENT_LLM_MODEL_NAME"])
            ),
            # memory_config=AgentMemoryConfig(history_rounds=4),
            use_vision=False
        )

        plan_agent = FlightPlanAgent(
            conf=agent_config_plan,
            name="plan_agent",
            system_prompt=get_flight_plan_agent_system_prompt(),
            mcp_servers=gaia_playwright_mcp_servers,
            mcp_config=gaia_playwright_mcp_config
        )

        execute_agent = GaiaPlayWrightAgent(
            conf=agent_config_execute,
            name="exec_agent",
            agent_id = "flight_search_agent",
            system_prompt=get_gaia_playwright_agent_system_prompt(),
            mcp_servers=gaia_playwright_mcp_servers,
            mcp_config=gaia_playwright_mcp_config
        )
        return TeamSwarm(plan_agent, execute_agent)

    @property
    def name(self):
        return self.agent_name()

    async def _save_task_result(self, context: ApplicationContext):
        return None

    def agent_name(self) -> str:
        return "FlightPlanGaiaPlayWrightAgent"
