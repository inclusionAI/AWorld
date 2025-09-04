# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from typing import List, Dict, Any, Callable

from aworld.core.exceptions import AWorldRuntimeException

from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel


class AggregatableAgent(Agent):
    """Support for results reprocess.

    The parameters of the extension function are the agent itself, which can obtain internal information of the agent.
    `processor_func` function example:
    >>> def process(agent: AggregatableAgent, res: Dict[str, Any]) -> ActionModel:
    >>>     ...
    """

    def __init__(self,
                 processor_func: Callable[['AggregatableAgent', Dict[str, Any]], ActionModel] = None,
                 **kwargs):
        super().__init__(**kwargs)
        self.processor_func = processor_func
        if not processor_func:
            raise AWorldRuntimeException("no processor_func in MultiResultAgent.")

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
        action = self.processor_func(self, observation.content)
        return [action]
