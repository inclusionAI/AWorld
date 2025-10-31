from typing import Dict, Any, List

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message


# Agent for writing and running code
class CodingAgent(Agent):
    """
    Agent for writing and running code
    """

    max_loop = 100

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        action_model_list = await super().async_policy(observation, info, message, **kwargs)
        return action_model_list

    async def should_terminate_loop(self, message: Message) -> bool:
        return self.loop_step >= self.max_loop

