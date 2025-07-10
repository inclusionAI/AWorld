# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from typing import List, Dict, Any, Callable

from aworld.utils.run_util import exec_agent

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel, Config


class ParallelizableAgent(Agent):
    """Support for parallel agents in the swarm.

    The parameters of the extension function are the agent itself, which can obtain internal information of the agent.
    `aggregate_func` function example:
    >>> def agg(agent: ParallelizableAgent, res: Dict[str, List[ActionModel]]):
    >>>     ...
    """

    def __init__(self,
                 conf: Config,
                 resp_parse_func: Callable[..., Any] = None,
                 agents: List[Agent] = [],
                 aggregate_func: Callable[..., Any] = None,
                 **kwargs):
        super().__init__(conf=conf, resp_parse_func=resp_parse_func, **kwargs)
        self.agents = agents
        # The function of aggregating the results of the parallel execution of agents.
        self.aggregate_func = aggregate_func

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
        tasks = []
        if self.agents:
            for agent in self.agents:
                tasks.append(asyncio.create_task(exec_agent(observation.content, agent, self.context, sub_task=True)))

        results = await asyncio.gather(*tasks)
        res = []
        for idx, result in enumerate(results):
            res.append(ActionModel(agent_name=self.agents[idx].id(), policy_info=result))
        return res

    def finished(self) -> bool:
        return all([agent.finished for agent in self.agents])
