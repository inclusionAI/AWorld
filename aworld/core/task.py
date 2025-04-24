# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import uuid
from dataclasses import dataclass, field
from typing import Any, Union, List, Dict, Callable

from pydantic import BaseModel

from aworld.config import ConfigDict
from aworld.core.agent.base import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.envs.tool import Tool, AsyncTool

Config = Union[Dict[str, Any], ConfigDict, BaseModel]


@dataclass
class Task:
    name: str = uuid.uuid1().hex
    input: Any = None
    # task config
    conf: Config = None
    # global tool instance
    tools: List[Union[Tool, AsyncTool]] = field(default_factory=list)
    # global tool names
    tool_names: List[str] = field(default_factory=list)
    # custom tool conf
    tools_conf: Config = field(default_factory=dict)
    # custom mcp servers conf
    mcp_servers_conf: Config = field(default_factory=dict)
    swarm: Swarm = None
    agent: Agent = None
    # for loop detect
    endless_threshold: int = 3


class Runner(object):
    __metaclass__ = abc.ABCMeta

    _use_demon: bool = False
    daemon_target: Callable[..., Any] = None

    async def pre_run(self):
        pass

    async def post_run(self):
        pass

    @abc.abstractmethod
    async def do_run(self):
        """Raise exception if not success."""

    async def _daemon_run(self):
        if self._use_demon and self.daemon_target and callable(self.daemon_target):
            import threading
            t = threading.Thread(target=self.daemon_target, name="daemon", daemon=True)
            t.start()

    async def run(self) -> Any:
        try:
            await self.pre_run()
            await self._daemon_run()
            ret = await self.do_run()
            return 0 if ret is None else ret
        except BaseException as ex:
            self._exception = ex
            # do record or report
            raise ex
        finally:
            await self.post_run()
