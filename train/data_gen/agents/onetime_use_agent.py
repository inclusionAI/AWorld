# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import traceback
from typing import Dict, Any, List

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger


class OnetimeUseAgent(Agent):
    """One time call, no need for complex processing and context dependencies."""

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        self._finished = False
        # llm input messages
        messages = await self.build_llm_input(observation, info, message)
        # call llm
        try:
            llm_response = await self.invoke_model(messages, message=message, **kwargs)
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        # parse llm output
        agent_result = await self.model_output_parser.parse(llm_response,
                                                            agent_id=self.id(),
                                                            use_tools_in_prompt=self.use_tools_in_prompt)

        self._finished = True
        # agent actions
        return agent_result.actions

    async def build_llm_input(self,
                              observation: Observation,
                              info: Dict[str, Any] = {},
                              message: Message = None,
                              **kwargs) -> List[Dict[str, Any]]:
        messages = []
        if self.system_prompt:
            messages.append({
                "role": "system",
                "content": self.system_prompt
            })

        messages.append({
            "role": "user",
            "content": f"""{observation.content}"""
        })
        return messages
