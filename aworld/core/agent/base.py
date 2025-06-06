# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import abc
import uuid


import aworld.trace as trace

from typing import Generic, TypeVar, Dict, Any, List, Tuple, Union

from pydantic import BaseModel

from aworld.config.conf import AgentConfig, load_config, ConfigDict
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.factory import Factory
from aworld.logs.util import logger
from aworld.sandbox.base import Sandbox

from aworld.utils.common import convert_to_snake, replace_env_variables

INPUT = TypeVar('INPUT')
OUTPUT = TypeVar('OUTPUT')


def is_agent_by_name(name: str) -> bool:
    return name in AgentFactory


def is_agent(policy: ActionModel) -> bool:
    return is_agent_by_name(policy.tool_name) or (not policy.tool_name and not policy.action_name)


class AgentStatus:
    # Init status
    START = 0
    # Agent is running for monitor or collection
    RUNNING = 1
    # Agent reject the task
    REJECT = 2
    # Agent is idle
    IDLE = 3
    # Agent meets exception
    ERROR = 4
    # End of one agent step
    DONE = 5
    # End of one task step
    FINISHED = 6


class AgentResult(BaseModel):
    current_state: Any
    actions: List[ActionModel]
    is_call_tool: bool = True


class MemoryModel(BaseModel):
    # TODO: memory module
    message: Dict = {}
    tool_calls: Any = None
    content: Any = None


class BaseAgent(Generic[INPUT, OUTPUT]):
    __metaclass__ = abc.ABCMeta

    def __init__(self,
                 conf: Union[Dict[str, Any],
                             ConfigDict, AgentConfig],
                 sandbox: Sandbox = None,
                 mcp_servers: List[str] = [],
                 mcp_config: Dict[str, Any] = {},
                 **kwargs
                 ):
        self.conf = conf
        if isinstance(conf, ConfigDict):
            pass
        elif isinstance(conf, Dict):
            self.conf = ConfigDict(conf)
        elif isinstance(conf, AgentConfig):
            # To add flexibility
            self.conf = ConfigDict(conf.model_dump())
        else:
            logger.warning(f"Unknown conf type: {type(conf)}")

        self._name = kwargs.pop("name", self.conf.get(
            "name", convert_to_snake(self.__class__.__name__)))
        self._desc = kwargs.pop("desc") if kwargs.get(
            "desc") else self.conf.get('desc', '')
        # Unique flag based agent name
        self.id = f"{self.name()}_{uuid.uuid1().hex[0:6]}"
        self.task = None
        # An agent can use the tool list
        self.tool_names: List[str] = kwargs.pop("tool_names", [])
        # An agent can delegate tasks to other agent
        self.handoffs: List[str] = kwargs.pop("agent_names", [])
        # Supported MCP server
        self.mcp_servers: List[str] = mcp_servers
        self.mcp_config: Dict[str, Any] = replace_env_variables(mcp_config)
        self.trajectory: List[Tuple[INPUT, Dict[str, Any], AgentResult]] = []
        # all tools that the agent can use. note: string name/id only
        self.tools = []
        self.context = Context.instance()
        self.state = AgentStatus.START
        self._finished = True
        # todo sandbox
        self.sandbox = sandbox or Sandbox(
            mcp_servers=self.mcp_servers, mcp_config=self.mcp_config)

        for k, v in kwargs.items():
            setattr(self, k, v)

    def name(self) -> str:
        return self._name

    def desc(self) -> str:
        return self._desc

    def run(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Message:
        with trace.span(self._name, run_type=trace.RunType.AGNET) as agent_span:
            self.pre_run()
            result = self.policy(observation, info, **kwargs)
            final_result = self.post_run(result, observation)
            return final_result if final_result else result

    async def async_run(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Message:
        with trace.span(self._name, run_type=trace.RunType.AGNET) as agent_span:
            await self.async_pre_run()
            result = await self.async_policy(observation, info, **kwargs)
            final_result = await self.async_post_run(result, observation)
            return final_result if final_result else result

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
        self.tool_names = options.get("tool_names", [])
        self.handoffs = options.get("agent_names", [])
        self.mcp_servers = options.get("mcp_servers", [])
        self.tools = []
        self.trajectory = []
        self._finished = True

    async def async_reset(self, options: Dict[str, Any]):
        """Clean agent instance state and reset."""
        self.task = options.get("task")

    @property
    def finished(self) -> bool:
        """Agent finished the thing, default is True."""
        return self._finished

    def pre_run(self):
        pass

    def post_run(self, policy_result: OUTPUT, input: INPUT) -> Message:
        pass

    async def async_pre_run(self):
        pass

    async def async_post_run(self, policy_result: OUTPUT, input: INPUT) -> Message:
        pass


class AgentManager(Factory):
    def __init__(self, type_name: str = None):
        super(AgentManager, self).__init__(type_name)
        self._agent_conf = {}
        self._agent_instance = {}

    def __call__(self, name: str = None, *args, **kwargs):
        if name is None:
            return self

        conf = self._agent_conf.get(name)
        if not conf:
            logger.warning(f"{name} not find conf in agent factory")
            conf = dict()
        elif isinstance(conf, BaseModel):
            conf = conf.model_dump()

        user_conf = kwargs.pop('conf', None)
        if user_conf:
            if isinstance(user_conf, BaseModel):
                conf.update(user_conf.model_dump())
            elif isinstance(user_conf, dict):
                conf.update(user_conf)
            else:
                logger.warning(
                    f"Unknown conf type: {type(user_conf)}, ignored!")

        conf['name'] = name
        conf = ConfigDict(conf)
        if name in self._cls:
            agent = self._cls[name](conf=conf, **kwargs)
            self._agent_instance[name] = agent
        else:
            raise ValueError(f"Can not find {name} agent!")
        return agent

    def desc(self, name: str) -> str:
        if self._agent_instance.get(name, None) and self._agent_instance[name].desc:
            return self._agent_instance[name].desc
        return self._desc.get(name, "")

    def agent_instance(self, name: str) -> BaseAgent | None:
        if self._agent_instance.get(name, None):
            return self._agent_instance[name]
        return None

    def register(self, name: str, desc: str, conf_file_name: str = None, **kwargs):
        """Register a tool to tool factory.

        Args:
            name: Tool name
            desc: Tool description
            supported_action: Tool abilities
            conf_file_name: Default tool config
        """
        res = super(AgentManager, self).register(name, desc, **kwargs)
        conf_file_name = conf_file_name if conf_file_name else f"{name}.yaml"
        conf = load_config(conf_file_name, kwargs.get("dir"))
        if not conf:
            logger.warning(f"{conf_file_name} not find, will use default")
            # use general tool config
            conf = AgentConfig().model_dump()
        self._agent_conf[name] = conf
        return res


AgentFactory = AgentManager("agent_type")
