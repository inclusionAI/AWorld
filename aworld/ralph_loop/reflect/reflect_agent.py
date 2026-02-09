# coding: utf-8
# Copyright (c) inclusionAI.
from dataclasses import asdict
from typing import Dict, Any, List

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.ralph_loop.reflect import Reflector, GeneralReflector
from aworld.ralph_loop.reflect.types import ReflectionResult, ReflectionInput


# Moving forward, consolidated into the `agents` package
class ReflectAgent(Agent):
    """ReflectAgent is an agent that uses a Reflector to generate a reflection of the current state."""

    def __init__(self, reflector: Reflector = None, **kwargs):
        super().__init__(**kwargs)
        llm_config = self.conf.llm_config
        self.reflector = reflector or GeneralReflector(model_config=llm_config)

    async def async_policy(self,
                           observation: Observation,
                           info: Dict[str, Any] = {},
                           message: Message = None,
                           **kwargs) -> List[ActionModel]:
        reflect_input = self._prepare_input(observation, info, message)
        result: ReflectionResult = await self.reflector.reflect(reflect_input)
        action = ActionModel(agent_name=self.id(), policy_info=asdict(result))
        return [action]

    def _prepare_input(self,
                       observation: Observation,
                       info: Dict[str, Any] = {},
                       message: Message = None) -> ReflectionInput:
        reflect_input = ReflectionInput(task_id=message.context.task_id,
                                        input_data=info.get('user_input', None),
                                        output_data=observation.content,
                                        reference_data=info.get('answer', None),
                                        iteration=observation.info.get('iteration', 0))
        return reflect_input
