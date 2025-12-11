# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Any, Callable

from aworld.agents.llm_agent import Agent


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
