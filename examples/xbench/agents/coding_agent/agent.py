from typing import Dict, Any, List

from aworld.agents.amni_llm_agent import ApplicationAgent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message


# 编写和运行代码Agent
class CodingAgent(ApplicationAgent):
    """
    # 编写和运行代码Agent
    """

    max_loop = 100

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        action_model_list = await super().async_policy(observation, info, message, **kwargs)
        self.get_task_context(message).put("coding_agent_result", action_model_list[0].policy_info)
        return action_model_list

    async def should_terminate_loop(self, message: Message) -> bool:
        return self.loop_step >= self.max_loop

