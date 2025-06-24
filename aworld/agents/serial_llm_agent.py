# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import List, Dict, Any

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.logs.util import logger


class SerialableAgent(Agent):
    """Support for serial execution of agents based on dependency relationships in the swarm."""
    agents: List[Agent] = []

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
        ob = observation
        message = None

        for agent in self.agents:
            message = await agent.async_run(ob, info, **kwargs)
            ob = self._action_to_observation(message.payload, agent.name())
        return message.payload

    def _action_to_observation(self, policy: List[ActionModel], agent_name: str):
        if not policy:
            logger.warning("no agent policy, will use default error info.")
            return Observation(content=f"{agent_name} no policy")

        # join all
        infos = [p.policy_info for p in policy]
        content = '\n'.join(infos)
        logger.debug(f"{content}")
        return Observation(content=content)

    def finished(self) -> bool:
        return all([agent.finished for agent in self.agents])
