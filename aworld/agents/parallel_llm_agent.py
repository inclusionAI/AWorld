# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from typing import List, Dict, Any, Callable

from aworld.agents.llm_agent import Agent
from aworld.core.agent.base import AgentResult
from aworld.core.common import Observation, ActionModel, Config
from aworld.core.model_output_parser import ModelOutputParser
from aworld.models.model_response import ModelResponse
from aworld.utils.run_util import exec_agent


class ParallelizableAgent(Agent):
    """Support for parallel agents in the swarm.

    The parameters of the extension function are the agent itself, which can obtain internal information of the agent.
    `aggregate_func` function example:
    >>> def agg(agent: ParallelizableAgent, res: Dict[str, List[ActionModel]]):
    >>>     ...
    """

    def __init__(self,
                 name: str,
                 conf: Config,
                 model_output_parser: ModelOutputParser[ModelResponse, AgentResult] = None,
                 agents: List[Agent] = None,
                 aggregate_func: Callable[['ParallelizableAgent',Dict[str, List[ActionModel]]], List[ActionModel]] = None,
                 **kwargs):
        super().__init__(name=name, conf=conf, model_output_parser=model_output_parser, **kwargs)
        self.agents = agents if agents else []
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

        if self.aggregate_func:
            res = self.aggregate_func(self, res)
        return res

    def finished(self) -> bool:
        return all([agent.finished for agent in self.agents])
