# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from typing import Any, Dict, Union, List

from aworld.config.common import Agents, Tools
from aworld.core.agent.base import Agent, AgentFactory
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.common import Observation, ActionModel


@AgentFactory.register(name=Agents.GYM.value, desc="gym agent")
class GymDemoAgent(Agent):
    """Example agent"""

    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        super(GymDemoAgent, self).__init__(conf, **kwargs)

    def policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        import numpy as np

        env_id = observation.info.get('env_id')
        if env_id != 'CartPole-v1':
            raise ValueError("Unsupported env")

        res = np.random.randint(2)
        action = [ActionModel(tool_name=Tools.GYM.value, action_name="play", params={"result": res})]
        if observation.info.get("done"):
            self._finished = True
        return action

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        return self.policy(observation, info, **kwargs)
