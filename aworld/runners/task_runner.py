# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import time
import uuid

from pydantic import BaseModel

from aworld.config import ConfigDict
from aworld.config.conf import ToolConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation
from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.tool.base import Tool, AsyncTool
from aworld.core.task import Runner, Task, TaskResponse
from aworld.logs.util import logger
from aworld import trace


class TaskRunner(Runner):
    """Task based runner api class."""
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

        self.context = Context()
        self.task = task
        self.daemon_target = kwargs.pop('daemon_target', None)
        self._use_demon = False if not task.conf else task.conf.get('use_demon', False)
        self._exception = None
        self.start_time = time.time()
        self.step_agent_counter = {}
        for k, v in kwargs.items():
            setattr(self, k, v)

    async def pre_run(self):
        task = self.task
        self.swarm = task.swarm
        self.input = task.input
        self.outputs = task.outputs
        self.name = task.name
        self.conf = task.conf if task.conf else ConfigDict()
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
        self.context.task_id = self.name
        self.context.trace_id = trace_id
        self.context.session = session

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
        self.swarm.event_driven = task.event_driven
        self.swarm.reset(observation.content, context=self.context, tools=self.tool_names)

    async def post_run(self):
        self.context.reset()

    @abc.abstractmethod
    async def do_run(self, context: Context = None) -> TaskResponse:
        """Task do run."""
