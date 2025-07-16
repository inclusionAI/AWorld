# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import List, Dict, Any, Callable

from aworld.utils.run_util import exec_agent

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel, Config
from aworld.logs.util import logger


class SerialableAgent(Agent):
    """Support for serial execution of agents based on dependency relationships in the swarm."""

    def __init__(self,
                 conf: Config,
                 resp_parse_func: Callable[..., Any] = None,
                 agents: List[Agent] = None,
                 **kwargs):
        super().__init__(conf=conf, resp_parse_func=resp_parse_func, **kwargs)
        self.agents = agents if agents else []

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
        action = ActionModel(agent_name=self.id(), policy_info=observation.content)
        if self.agents:
            for agent in self.agents:
                result = await exec_agent(observation.content, agent, self.context, sub_task=True)
                if result:
                    if result.success:
                        con = result.answer
                    else:
                        con = result.msg
                    action = ActionModel(agent_name=agent.id(), policy_info=con)
                    observation = self._action_to_observation(action, agent.name())
                else:
                    raise Exception(f"{agent.id()} execute fail.")
        return [action]

    def _action_to_observation(self, policy: ActionModel, agent_name: str):
        if not policy:
            logger.warning("no agent policy, will use default error info.")
            return Observation(content=f"{agent_name} no policy")

        logger.debug(f"{policy.policy_info}")
        return Observation(content=policy.policy_info)

    def finished(self) -> bool:
        return all([agent.finished for agent in self.agents])
