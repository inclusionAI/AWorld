from typing import Dict, Any, List

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.output import Output
from aworldappinfra.ui.out_put_tools import build_logo_output


class GaiaAgent(Agent):

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {},
                           message: Message = None, **kwargs) -> List[ActionModel]:
        action_model_list = await super().async_policy(observation, info, message, **kwargs)

        content = action_model_list[0].policy_info
        return action_model_list