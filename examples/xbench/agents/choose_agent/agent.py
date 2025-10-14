from typing import Dict, Any, List

from aworld.agents.amni_llm_agent import ApplicationAgent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger


class ChooseAgent(ApplicationAgent):

    max_loop=50

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:

        action_model_list = await super().async_policy(observation, info, message, **kwargs)
        if self._finished:
            logger.info(f"[ChooseAgent] add final_result to context : {action_model_list[0].policy_info}")
            self.get_task_context(message).put("final_result", action_model_list[0].policy_info)
        if self._finished and not action_model_list[0].policy_info:
            action_model_list[0].policy_info += "\n\n" + observation.content
        return action_model_list

    async def should_terminate_loop(self, message: Message) -> bool:
        return self.loop_step >= self.max_loop

