# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import List, Dict, Any, Callable

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel, Config
from aworld.logs.util import logger


class SerialableAgent(Agent):
    """Support for serial execution of agents based on dependency relationships in the swarm."""

    def __init__(self,
                 conf: Config,
                 resp_parse_func: Callable[..., Any] = None,
                 agents: List[Agent] = [],
                 **kwargs):
        super().__init__(conf=conf, resp_parse_func=resp_parse_func, **kwargs)
        self.agents = agents

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
        from aworld.config import RunConfig
        from aworld.core.task import Task
        from aworld.runners.utils import choose_runners, execute_runner

        action = ActionModel(agent_name=self.id(), policy_info=observation.content)
        for agent in self.agents:
            task = Task(is_sub_task=True, input=observation, agent=agent, context=self.context)
            runners = await choose_runners([task])
            res = await execute_runner(runners, RunConfig(reuse_process=False))
            if res:
                v = res.get(task.id)
                action = ActionModel(agent_name=self.id(), policy_info=v.answer)
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
