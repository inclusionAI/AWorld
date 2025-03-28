# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import abc
import uuid
from typing import Generic, TypeVar, Dict, Any, List, Tuple, Union

from aworld.models.llm import get_llm_model
from pydantic import BaseModel

from aworld.config.conf import AgentConfig
from aworld.core.common import Observation, ActionModel
from aworld.core.factory import Factory

INPUT = TypeVar('INPUT')
OUTPUT = TypeVar('OUTPUT')


class Agent(Generic[INPUT, OUTPUT]):
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf: AgentConfig, **kwargs):
        # Unique flag based agent name
        self.id = f"{self.name()}_{uuid.uuid1().hex[0:6]}"
        self.conf = conf
        if conf:
            self.dict_conf = conf.model_dump()
        else:
            self.dict_conf = dict()
        self.task = None
        # An agent can use the tool list
        self.tool_names: List[str] = kwargs.get("tool_names")
        # An agent can delegate tasks to other agent
        self.handoffs: List[str] = kwargs.get("agent_names", [])
        self.trajectory: List[Tuple[INPUT, Dict[str, Any], AgentResult]] = []
        self._finished = False

        for k, v in kwargs.items():
            setattr(self, k, v)

    @abc.abstractmethod
    def name(self) -> str:
        """Agent name that must be implemented in subclasses"""

    @abc.abstractmethod
    def policy(self, observation: INPUT, info: Dict[str, Any] = None, **kwargs) -> OUTPUT:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.
        """

    @abc.abstractmethod
    async def async_policy(self, observation: INPUT, info: Dict[str, Any] = None, **kwargs) -> OUTPUT:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.
        """

    def reset(self, options: Dict[str, Any]):
        """Clean agent instance state and reset."""
        if options is None:
            options = {}
        self.task = options.get("task")
        self.tool_names = options.get("tool_names")
        self.handoffs = options.get("agent_names", [])
        self.trajectory = []
        self._finished = False

    async def async_reset(self, options: Dict[str, Any]):
        """Clean agent instance state and reset."""
        self.task = options.get("task")

    @property
    def finished(self) -> bool:
        """Agent finished the thing, default is True."""
        return self._finished


class BaseAgent(Agent[Observation, Union[Observation, List[ActionModel]]]):
    """Basic agent for unified protocol within the framework."""

    def __init__(self, conf: AgentConfig, **kwargs):
        super(BaseAgent, self).__init__(conf, **kwargs)
        self.model_name = conf.llm_model_name
        self._llm = None

    @property
    def llm(self):
        # lazy
        if self._llm is None:
            self._llm = get_llm_model(self.conf)
        return self._llm

    @abc.abstractmethod
    def policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """

    @abc.abstractmethod
    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """


class AgentManager(Factory):
    def __call__(self, name: str = None, *args, **kwargs):
        if name is None:
            return self

        # Agent must have conf params
        if 'conf' not in kwargs:
            if not args:
                raise ValueError("params `conf` must in args or kwargs!")
            else:
                conf = args[0]
        else:
            conf = kwargs.pop('conf')
            if conf is None:
                raise ValueError("params `conf` must in args or kwargs!")

        if name in self._cls:
            agent = self._cls[name](conf=conf, **kwargs)
        else:
            raise ValueError(f"Can not find {name} agent!")
        return agent


AgentFactory = AgentManager("agent_type")


class AgentResult(BaseModel):
    current_state: Any
    actions: List[ActionModel]
