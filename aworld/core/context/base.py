# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from collections import Counter, OrderedDict
from typing import Dict, List, Any, Optional, Callable
from threading import local
from dataclasses import dataclass

from aworld.config import ConfigDict
from aworld.config.conf import ContextRuleConfig
from aworld.core.context.session import Session
from aworld.core.singleton import InheritanceSingleton
from aworld.utils.common import nest_dict_counter

@dataclass
class ContextUsage:
    total_context_length: int = 128000
    used_context_length: int = 0
    def __init__(self, total_context_length: int = 128000, used_context_length: int = 0):
        self.total_context_length = total_context_length
        self.used_context_length = used_context_length

@dataclass
class AgentContext:
    """agent running context"""
    agent_id: str = None
    agent_name: str = None
    agent_desc: str = None
    system_prompt: str = None
    agent_prompt: str = None
    tool_names: List[str] = None
    step: int = 0
    context_rule: ContextRuleConfig = None
    # mutable from memory
    messages: List[Dict[str, Any]] = None
    # mutable context length usage
    context_usage: ContextUsage = None

    def __init__(self, 
                 agent_id: str = None,
                 agent_name: str = None,
                 agent_desc: str = None,
                 system_prompt: str = None,
                 agent_prompt: str = None,
                 tool_names: List[str] = None,
                 step: int = 0,
                 context_rule: ContextRuleConfig = None,
                 messages: List[Dict[str, Any]] = None,
                 context_usage: ContextUsage = None):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.agent_desc = agent_desc
        self.system_prompt = system_prompt
        self.agent_prompt = agent_prompt
        self.tool_names = tool_names if tool_names is not None else []
        self.step = step
        self.context_rule = context_rule
        self.messages = messages if messages is not None else []
        self.context_usage = context_usage if context_usage is not None else ContextUsage()

    def __post_init__(self):
        if self.tool_names is None:
            self.tool_names = []
        if self.messages is None:
            self.messages = []
    
    def set_messages(self, messages: List[Dict[str, Any]]):
        self.messages = messages

class Context(InheritanceSingleton):
    def __init__(self, user: str = None, **kwargs):
        self._user = user
        self._init(**kwargs)

    def _init(self, **kwargs):
        self._task_id = kwargs.get('task_id')
        self._engine = kwargs.get('engine')
        self._trace_id = kwargs.get('trace_id')
        self._session: Session = kwargs.get('session')
        self.context_info = ConfigDict()
        self.agent_info = ConfigDict()
        self.trajectories = OrderedDict()
        self._token_usage = {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
        }
        self._current_agent_context_map = {}
        # TODO swarm topology
        # TODO EventManager
        # TODO workspace

    def add_token(self, usage: Dict[str, int]):
        self._token_usage = nest_dict_counter(self._token_usage, usage)

    def reset(self, **kwargs):
        self._init(**kwargs)
        self._current_agent_context_map = {}

    def check_agent_id(self, agent_id: str):
        if agent_id is None:
            raise ValueError("agent_id is required")

    def set_current_agent_context(self, agent_id: str, agent_context: AgentContext):
        self.check_agent_id(agent_id)
        self._current_agent_context_map[agent_id] = agent_context

    def get_current_agent_context(self, agent_id: str) -> Optional[AgentContext]:
        self.check_agent_id(agent_id)
        return self._current_agent_context_map.get(agent_id, None)

    def clear_current_agent_context(self, agent_id: str):
        self.check_agent_id(agent_id)
        self._current_agent_context_map[agent_id] = None

    def get_current_agent_usage(self, agent_id: str):
        self.check_agent_id(agent_id)
        return self.get_current_agent_context(agent_id).context_usage

    def update_current_agent_context_usage(self, agent_id: str, used_context_length: int):
        self.check_agent_id(agent_id)
        self.get_current_agent_context(agent_id).context_usage.used_context_length = used_context_length

    @property
    def trace_id(self):
        return self._trace_id

    @trace_id.setter
    def trace_id(self, trace_id):
        self._trace_id = trace_id

    @property
    def token_usage(self):
        return self._token_usage

    @property
    def engine(self):
        return self._engine

    @property
    def user(self):
        return self._user

    @user.setter
    def user(self, user):
        if user is not None:
            self._user = user

    @property
    def task_id(self):
        return self._task_id

    @task_id.setter
    def task_id(self, task_id):
        if task_id is not None:
            self._task_id = task_id

    @property
    def session_id(self):
        if self.session:
            return self.session.session_id
        else:
            return None

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, session: Session):
        self._session = session

    @property
    def record_path(self):
        return "."

    @property
    def is_task(self):
        return True

    @property
    def enable_visible(self):
        return False

    @property
    def enable_failover(self):
        return False

    @property
    def enable_cluster(self):
        return False
