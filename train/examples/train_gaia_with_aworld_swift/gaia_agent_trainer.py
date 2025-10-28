# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import uuid
from typing import Union

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.agent.swarm import Swarm
from train.adapter.swift.aworld_agent_trainer import AworldTrainer

GAIA_SYSTEM_PROMPT = """
You are an all-capable AI assistant, aimed at solving any task presented by the user.
"""


class GaiaTrainer(AworldTrainer):
    def build_agents(self) -> Union[Agent, Swarm]:
        return Agent(
            conf=AgentConfig(
                llm_model_name="dummy",
                llm_api_key="dummy",
                llm_base_url="dummy",
                llm_provider="swift",
                params={"client": self,
                        "tokenizer": self.tokenizer,
                        "request_id": uuid.uuid4().hex,
                        "tool_parser": "hermes"}
            ),
            name="gaia_super_agent",
        )

