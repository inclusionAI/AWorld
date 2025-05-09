# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import uuid

from pydantic import BaseModel
from aworld.config.conf import ToolConfig
from aworld.core.agent.base import Agent, is_agent_by_name
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.envs.tool import Tool, AsyncTool
from aworld.core.task import Runner, Task
from aworld.logs.util import logger, trace_logger
from aworld import trace


class TaskRunner(Runner):
    """Task based runner base class."""
    __metaclass__ = abc.ABCMeta

    def __init__(self, task: Task, *args, **kwargs):
        if task.tools is None:
            task.tools = []
        if task.tool_names is None:
            task.tool_names = []

        if not task.agent and not task.swarm:
            raise ValueError("agent and swarm all is None.")
        if task.agent and task.swarm:
            raise ValueError("agent and swarm choose one only.")
        if task.agent:
            # uniform agent
            task.swarm = Swarm(task.agent)

        if task.conf is None:
            task.conf = dict()
        if isinstance(task.conf, BaseModel):
            task.conf = task.conf.model_dump()
        check_input = task.conf.get("check_input", False)
        if check_input and not task.input:
            raise ValueError("task no input")

        self.task = task
        self.daemon_target = kwargs.pop('daemon_target', None)
        self._use_demon = False if not task.conf else task.conf.get('use_demon', False)
        self._exception = None
        for k, v in kwargs.items():
            setattr(self, k, v)

    async def pre_run(self):
        task = self.task
        self.swarm = task.swarm
        self.input = task.input
        self.outputs = task.outputs
        self.name = task.name
        self.conf = task.conf
        self.tools = {tool.name(): tool for tool in task.tools} if task.tools else {}
        task.tool_names.extend(self.tools.keys())
        # lazy load
        self.tool_names = task.tool_names
        self.tools_conf = task.tools_conf
        if self.tools_conf is None:
            self.tools_conf = {}
        # mcp performs special process, use async only in the runn
        self.tools_conf['mcp'] = ToolConfig(use_async=True, name='mcp')
        self.endless_threshold = task.endless_threshold

        # build context
        if task.session_id:
            session = Session(session_id=task.session_id)
        else:
            session = Session(session_id=uuid.uuid1().hex)
        trace_id = uuid.uuid1().hex if trace.get_current_span() is None else trace.get_current_span().get_trace_id()
        self.context = Context(task_id=self.name, trace_id=trace_id, session=session)

        # init tool state by reset(), and ignore them observation
        observation = None
        if self.tools:
            for _, tool in self.tools.items():
                # use the observation and info of the last one
                if isinstance(tool, Tool):
                    tool.context = self.context
                    observation, info = tool.reset()
                elif isinstance(tool, AsyncTool):
                    observation, info = await tool.reset()
                else:
                    logger.warning(f"Unsupported tool type: {tool}, will ignored.")

        if observation:
            if not observation.content:
                observation.content = self.input
        else:
            observation = Observation(content=self.input)

        self.observation = observation
        self.swarm.reset(observation.content, context=self.context, tools=self.tool_names)

    def is_agent(self, policy: ActionModel):
        return is_agent_by_name(policy.tool_name) or (not policy.tool_name and not policy.action_name)
