# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Any, Callable, List, Dict, Optional

from aworld.agents.llm_agent import Agent
from aworld.core.context.base import Context
from aworld.logs.util import logger
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryItem


class LoopableAgent(Agent):
    """Support for loop agents in the swarm.

    The parameters of the extension function are the agent itself, which can obtain internal information of the agent.
    `stop_func` function example:
    >>> def stop(agent: LoopableAgent):
    >>>     ...

    `loop_point_finder` function example:
    >>> def find(agent: LoopableAgent):
    >>>     ...
    """

    def __init__(self,
                 name: str,
                 max_run_times: int = 1,
                 loop_point: str = None,
                 loop_point_finder: Callable[..., Any] = None,
                 stop_func: Callable[..., Any] = None,
                 *args,
                 **kwargs):
        """Initialize LoopableAgent.

        Args:
            name: Agent name.
            max_run_times: Maximum number of loop runs.
            loop_point: The loop point (agent name) for the loop agent.
            loop_point_finder: Function to determine the loop point for multiple loops.
            stop_func: Function to determine if the loop should stop.
            *args: Additional positional arguments passed to parent Agent.
            **kwargs: Additional keyword arguments passed to parent Agent.
        """
        super().__init__(name, *args, **kwargs)
        self.max_run_times = max_run_times
        self.cur_run_times = 0
        self.loop_point = loop_point
        self.loop_point_finder = loop_point_finder
        self.stop_func = stop_func

    def _process_messages(self, messages: List[Dict[str, Any]],
                          context: Context = None) -> Optional[List[Dict[str, Any]]]:
        # default handling for loop agents
        # The content of the last two messages is the same
        if len(messages) > 1 and messages[-1]["content"] == messages[-2]["content"]:
            def_con = "Your answer is either incorrect. Please read the original question carefully, check and analyze it, and try to answer it again."
            # modify message
            messages[-1]['content'] = def_con
            # modify memory, keep consistent
            agent_memory_config = self.memory_config
            if self._is_amni_context(context):
                agent_context_config = context.get_config().get_agent_context_config(self.id())
                agent_memory_config = agent_context_config.to_memory_config()
            filters = self._build_memory_filters(context, additional_filters={"memory_type": "message"})
            memory = MemoryFactory.instance()
            histories: List[MemoryItem] = memory.get_last_n(last_rounds=2,
                                                            filters=filters,
                                                            agent_memory_config=agent_memory_config)
            histories[-1].content = def_con
            memory.update(histories[-1])
            logger.info(f"{self.name()} {self.cur_run_times} times to modify message for loop!")

        return messages

    @property
    def goto(self):
        """The next loop point is what the loop agent wants to reach."""
        if self.loop_point_finder:
            return self.loop_point_finder(self)
        if self.loop_point:
            return self.loop_point
        return self.id()

    @property
    def finished(self) -> bool:
        """Loop agent termination state detection, achieved loop count or termination condition."""
        if self.cur_run_times >= self.max_run_times or (self.stop_func and self.stop_func(self)):
            self._finished = True
            return True

        self._finished = False
        return False
