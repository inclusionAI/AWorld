# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import List, Dict, Any

from aworld.core.agent.base import BaseAgent, AgentFactory
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger
from train.data_gen.schema import GeneratedTool
from train.data_gen.tool_repository import ToolRepository


@AgentFactory.register(name="tool_select_agent", desc="A tool select strategy agent")
class ToolSelectAgent(BaseAgent):
    """Rule Agent, no predict model."""

    def __init__(self, tool_repository: ToolRepository, **kwargs):
        kwargs["name"] = kwargs.get("name", "tool_select_agent")
        kwargs["description"] = kwargs.get("description", "A tool strategy agent")
        super().__init__(**kwargs)

        self.count = self.conf.ext.get("count", 3)
        self.tool_repository = tool_repository

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        # load file
        if not self.tool_repository:
            file_path = observation.info.get("file_path")
            logger.info(f"load from {file_path}")
            self.tool_repository = ToolRepository()
            if not file_path:
                raise ValueError("No tools to select, need add tools to the ToolRepository.")
            await self.tool_repository.load_from_file(file_path)

        # random strategy
        tools: List[GeneratedTool] = await self.tool_repository.get_by_random(base_count=self.count,
                                                                              use_random_count=True)
        return [ActionModel(policy_info=tools, agent_name=self.id())]
