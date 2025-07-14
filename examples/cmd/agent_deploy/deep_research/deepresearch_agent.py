# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import logging
import os
from typing import Optional

from aworld.agents.llm_agent import Agent
from aworld.config.conf import TaskConfig, AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.task import Task
from pydantic import BaseModel

from .sub_agents.plan_agent import create_plan_agent
from .sub_agents.reasoning_loop_agent import create_reasoning_loop_agent
from .sub_agents.reporting_agent import create_reporting_agent
from .deepresearch_sub_agent_v1.plan_agent import PlanAgent
from .deepresearch_sub_agent_v1.reasoning_loop_agent import ReasoningLoopAgent
from .deepresearch_sub_agent_v1.reporting_agent import ReportingAgent
from .deepresearch_sub_agent_v1.web_search_agent import WebSearchAgent
from .base_agent import AworldBaseAgent


class Pipeline(AworldBaseAgent):
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        logging.info("deepresearch_agent init success")

    async def build_swarm(self, body):
        agent_config = AgentConfig(
            llm_config=ModelConfig(
                llm_provider="openai",
                llm_model_name=os.getenv("LLM_MODEL_NAME"),
                llm_base_url=os.getenv("LLM_BASE_URL"),
                llm_api_key=os.getenv("LLM_API_KEY")
            ),
            use_vision=False
        )

        plan_agent = PlanAgent(
            name="plan_agent",
            desc="plan_agent",
            conf=agent_config,
        )

        web_search_agent = WebSearchAgent(
            name="web_search_agent",
            desc="web_search_agent",
            conf=agent_config,
            mcp_servers=["aworldsearch-server"],
            mcp_config=json.load(open(os.path.join(os.path.dirname(__file__), "mcp.json"))),
        )

        reasoning_loop_agent = ReasoningLoopAgent(
            name="reasoning_loop_agent",
            desc="reasoning_loop_agent",
            conf=agent_config
        )

        reporting_agent = ReportingAgent(
            name="reporting_agent",
            desc="reporting_agent",
            conf=agent_config
        )

        return Swarm(plan_agent, web_search_agent, reasoning_loop_agent, reporting_agent,
                     sequence=True, event_driven=True)

    async def build_task(self, agent: Optional[Agent],swarm: Optional[Swarm], task_id, user_input, user_message, body):
        task = Task(
            id=str(task_id),
            swarm=swarm,
            input=user_input,
            endless_threshold=5,
            conf=TaskConfig(exit_on_failure=True)
        )
        return task

    def agent_name(self) -> str:
        return "deepresearch_agent"
