# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import abc
import traceback
from typing import Dict, Tuple, Any, TypeVar, Generic, List, Union

from aworld.config.conf import ToolConfig
from aworld.core.action import ToolAction
from aworld.core.action_factory import ActionFactory
from aworld.core.common import Observation, ToolActionModel, ActionResult, Tools
from aworld.core.factory import Factory
from aworld.logs.util import logger

AgentInput = TypeVar("AgentInput")
ToolInput = TypeVar("ToolInput")


class EnvTool(Generic[AgentInput, ToolInput]):
    """The basic generic classes of tools in the environment, with two parameterized types: AgentInput and ToolInput.

    We follow the gym/gymnasium protocol to be compatible with gym games, can also build special env tool in the framework.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf, **kwargs) -> None:
        self.conf = conf
        self.dict_conf = conf.model_dump()
        for k, v in kwargs.items():
            setattr(self, k, v)
        action_executor.register(name=self.name(), tool=self)
        self.action_executor = action_executor

    @abc.abstractmethod
    def name(self):
        """Tool unique name."""

    @abc.abstractmethod
    def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        AgentInput, dict[str, Any]]:
        """Resets the initial internal state, returning an initial state and extended info."""

    @abc.abstractmethod
    def step(self, action: ToolInput, **kwargs) -> Tuple[AgentInput, float, bool, bool, Dict[str, Any]]:
        """Run one step of the tool's in env using the actions.

        Args:
            action(ToolInput): Actions provided by the agent to update the observation.
        Return:
            Quintuple，key information: AgentInput and extended info dict.
        """

    @abc.abstractmethod
    def finished(self) -> bool:
        """The final execution status of the task from agent instructions."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the tool resources in the environment."""

    def render(self):
        """For interface compatibility."""
        pass


class AsyncEnvTool(Generic[AgentInput, ToolInput]):
    """The basic generic classes of tools in the environment, with two parameterized types: AgentInput and ToolInput.

    We follow the gym/gymnasium protocol to be compatible with gym games, can also build special env tool in the framework.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        self.conf = conf
        self.dict_conf = conf.model_dump()
        for k, v in kwargs.items():
            setattr(self, k, v)
        action_executor.register(name=self.name(), tool=self)
        self.action_executor = action_executor

    @abc.abstractmethod
    def name(self):
        """Tool unique name."""

    @abc.abstractmethod
    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        AgentInput, dict[str, Any]]:
        """Resets the initial internal state, returning an initial state and extended info."""

    @abc.abstractmethod
    async def step(self, action: ToolInput, **kwargs) -> Tuple[AgentInput, float, bool, bool, Dict[str, Any]]:
        """Run one step of the tool's in env using the actions.

        Args:
            action(ToolInput): Actions provided by the agent to update the observation.
        Return:
            Quintuple，key information: AgentInput and extended info dict.
        """

    @abc.abstractmethod
    async def finished(self) -> bool:
        """The final execution status of the task from agent instructions."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Close the tool resources in the environment."""

    async def render(self):
        """For interface compatibility."""
        pass


class ToolsManager(Factory):
    def __init__(self, type_name: str = None):
        super(ToolsManager, self).__init__(type_name)
        self._tool_with_action = {}

    def __call__(self, name: str = None, *args, **kwargs):
        if name is None:
            return self

        if 'conf' not in kwargs:
            if not args:
                raise ValueError("params `conf` must in args or kwargs!")
            else:
                conf = args[0]
        else:
            conf = kwargs.pop('conf')
            if conf is None:
                raise ValueError("params `conf` must in args or kwargs!")

        asyn = kwargs.pop("asyn", False)
        name = "async_" + name if asyn else name
        if name in self._cls:
            tool = self._cls[name](conf=conf, **kwargs)
        else:
            # default browser env tool
            logger.warning("Empty tool name, default use 'browser'")
            asyn = kwargs.get('async', False)
            if asyn:
                name = "async_" + Tools.BROWSER.value
            else:
                name = Tools.BROWSER.value
            tool = self._cls[name](conf=conf, **kwargs)
        action_executor.register(name, tool)
        return tool

    def get_tool_action(self, tool: str, asyn: bool = False):
        if asyn:
            tool = "async_" + tool
        return self._tool_with_action.get(tool)

    def register(self, name: str, desc: str, supported_action: ToolAction, **kwargs):
        res = super(ToolsManager, self).register(name, desc, **kwargs)
        asyn = kwargs.pop("asyn", False)
        prefix = "async_" if asyn else ""
        self._tool_with_action[prefix + name] = supported_action
        return res


ToolFactory = ToolsManager("env_tool_type")


class ToolActionExecutor(object):
    __metaclass__ = abc.ABCMeta

    def __init__(self, env_tool: EnvTool[Observation, List[ToolActionModel]] = None):
        self.tool = env_tool
        self.tools: Dict[str, EnvTool[Observation, List[ToolActionModel]]] = {}

    def register(
            self,
            name: str,
            tool: Union[EnvTool[Observation, List[ToolActionModel]], AsyncEnvTool[Observation, List[ToolActionModel]]]):
        self.tools[name] = tool

    @abc.abstractmethod
    def execute_action(self, actions: List[ToolActionModel], **kwargs) -> Tuple[List[ActionResult], Any]:
        """"""
        return self.execute_env_action(actions, self.tool, **kwargs)

    @abc.abstractmethod
    async def async_execute_action(self, actions: List[ToolActionModel], **kwargs) -> Tuple[List[ActionResult], Any]:
        """"""
        return await self.async_execute_env_action(actions, self.tool, **kwargs)

    @abc.abstractmethod
    def execute_env_action(self,
                           actions: List[ToolActionModel],
                           tool: EnvTool[Observation, List[ToolActionModel]],
                           **kwargs) -> Tuple[List[ActionResult], Any]:
        """"""
        action_results = []
        ctx = None
        for action in actions:
            if action is None:
                logger.warning("empty action, ignore it.")
                continue

            if tool is None:
                tool_name = action.tool_name
                tool = self.tools.get(tool_name)
                if tool is None:
                    tool = ToolFactory(tool_name, conf=kwargs.get("conf", ToolConfig()))
                    self.tools[tool_name] = tool

            try:
                action_result, ctx = self.do_act(action, tool, **kwargs)
                action_results.append(action_result)
            except:
                logger.warning(traceback.format_exc())
        return action_results, ctx

    async def async_execute_env_action(self,
                                       actions: List[ToolActionModel],
                                       tool: EnvTool[Observation, List[ToolActionModel]],
                                       **kwargs) -> Tuple[List[ActionResult], Any]:
        """"""
        action_results = []
        ctx = None
        for action in actions:
            if action is None:
                logger.warning("empty action, ignore it.")
                continue

            if tool is None:
                tool_name = "async_" + action.tool_name
                tool = self.tools.get(tool_name)
                if tool is None:
                    tool = ToolFactory(tool_name, conf=kwargs.get("conf", ToolConfig()))
                    self.tools[tool_name] = tool
            try:
                action_result, ctx = await self.async_do_act(action, tool, **kwargs)
                action_results.append(action_result)
            except:
                logger.warning(traceback.format_exc())
        return action_results, ctx

    def do_act(self, action_model: ToolActionModel, tool: EnvTool[Observation, List[ToolActionModel]], **kwargs):
        action_name = action_model.action_name
        if action_name not in ActionFactory:
            action_name = action_model.tool_name + action_model.action_name
            if action_name not in ActionFactory:
                raise ValueError(f'Action {action_name} not found in ActionFactory')

        action = ActionFactory(action_name)
        action_result, page = action.act(action_model, tool=tool, **kwargs)
        logger.info(f"{tool.name()}-{action_name} execute finished")
        return action_result, page

    async def async_do_act(self, action_model: ToolActionModel, tool: EnvTool[Observation, List[ToolActionModel]],
                           **kwargs):
        action_name = action_model.action_name
        if action_name not in ActionFactory:
            action_name = action_model.tool_name + action_model.action_name
            if action_name not in ActionFactory:
                raise ValueError(f'Action {action_name} not found in ActionFactory')

        action = ActionFactory(action_name)
        action_result, page = await action.async_act(action_model, tool=tool, **kwargs)
        logger.info(f"{tool.name()}-{action_name} execute finished")
        return action_result, page


action_executor = ToolActionExecutor()
