# coding: utf-8
# Copyright (c) inclusionAI.
import json
import traceback
from typing import List, Any, Dict

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger
from train.data_gen.agents.prompts import tool_orchestra_agent_system_prompt
from train.data_gen.tool_repository import ToolRepository


class ToolOrchestratorAgent(Agent):
    def __init__(self, tool_repository: ToolRepository, **kwargs):
        kwargs['name'] = kwargs.get('name', 'tool_orchestra_agent')
        kwargs['description'] = kwargs.get('description', 'Produce tools call chain based on tool list.')
        kwargs['system_prompt'] = kwargs.get(
            'system_prompt',
            tool_orchestra_agent_system_prompt
        )
        super().__init__(**kwargs)

        self.tool_repository = tool_repository

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        # No context information required, all at once
        self._finished = False
        messages = await self.build_llm_input(observation, info, message)
        try:
            llm_response = await self.invoke_model(messages, message=message, **kwargs)
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e

        agent_result = await self.output_converter.parse(llm_response,
                                                         agent_id=self.id(),
                                                         use_tools_in_prompt=self.use_tools_in_prompt)

        self._finished = True
        if agent_result.actions:
            # if it has policy info, parse and use the first one
            if agent_result.actions[0].policy_info:
                logger.info(f"{self.id()} policy info: {agent_result.actions[0].policy_info}")
                json_con = json.loads(agent_result.actions[0].policy_info)
                if json_con:
                    exec_graph = json_con.get('execution_graph')
                    if exec_graph:
                        return agent_result.actions
                    else:
                        entities_analysis = json_con.get('entities_analysis')
                        if not entities_analysis:
                            # re generate
                            # return to tool sample generation
                            pass
                        else:
                            # TODO: generate execution graph
                            pass

        logger.warning(f"{self.id()} no action to the next node, all finished.")
        return []

    async def build_llm_input(self,
                              observation: Observation,
                              info: Dict[str, Any] = {},
                              message: Message = None,
                              **kwargs) -> List[Dict[str, Any]]:
        # one time execution
        messages = [
            {
                "role": "system",
                "content": self.system_prompt
            },
            {
                "role": "user",
                "content": f"""Please based on the following information:\n{observation.content}"""
            }
        ]
        return messages
