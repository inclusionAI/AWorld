import re
from typing import Dict, Any, List

from aworld.agents.loop_llm_agent import LoopableAgent
from aworld.core.agent.base import AgentResult
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.event.base import Message, MemoryEventType as MemoryType, MemoryEventMessage
from aworld.core.exceptions import AWorldRuntimeException
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryAIMessage, MemoryMessage, MemoryToolMessage
from aworld.core.event.base import Message
from aworld.models.model_response import ModelResponse


class FlightSearchAgent(LoopableAgent):
    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {},
                           message: Message = None, **kwargs) -> List[ActionModel]:
        action_model_list = await super().async_policy(observation, info, message, **kwargs)

        content = action_model_list[0].policy_info
        return action_model_list

    def is_agent_finished(self, llm_response: ModelResponse, agent_result: AgentResult) -> bool:
        # 正则匹配llm_response中是否包含<answer>{answer}</answer>块
        if llm_response.content:
            pattern = r'<answer>.*?</answer>'
            if re.search(pattern, llm_response.content, re.DOTALL):
                self._finished = True

        # if not agent_result.is_call_tool:
        #     self._finished = True
        return self.finished
