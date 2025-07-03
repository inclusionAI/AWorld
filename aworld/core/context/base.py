# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, TYPE_CHECKING, Union
from pydantic import BaseModel, Field
import copy

from aworld.config import ConfigDict
from aworld.config.conf import ContextRuleConfig, ModelConfig
from aworld.core.context.context_state import ContextState
from aworld.core.context.session import Session
from aworld.core.singleton import InheritanceSingleton
from aworld.models.model_response import ModelResponse
from aworld.utils.common import nest_dict_counter

if TYPE_CHECKING:
    from aworld.core.task import Task


@dataclass
class ContextUsage:
    total_context_length: int = 128000
    used_context_length: int = 0

    def __init__(self, total_context_length: int = 128000, used_context_length: int = 0):
        self.total_context_length = total_context_length
        self.used_context_length = used_context_length


class Context():
    """Single instance, can use construction or `instance` static method to create or get `Context` instance.

    Examples:
        >>> context = Context()
    """

    def __init__(self,
                 user: str = None,
                 task_id: str = None,
                 trace_id: str = None,
                 session: Session = None,
                 engine: str = None,
                 **kwargs):

        super().__init__()
        self._user = user
        self._init(task_id=task_id, trace_id=trace_id,
                   session=session, engine=engine, **kwargs)

    def _init(self, *, task_id: str = None, trace_id: str = None, session: Session = None, engine: str = None):
        self._task_id = task_id
        self._task = None
        self._engine = engine
        self._trace_id = trace_id
        self._session: Session = session
        self.context_info = ContextState()
        self.agent_info = ConfigDict()
        self.trajectories = OrderedDict()
        self._token_usage = {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
        }
        # TODO workspace
        self._swarm = None
        self._event_manager = None

    def add_token(self, usage: Dict[str, int]):
        self._token_usage = nest_dict_counter(self._token_usage, usage)

    def reset(self, **kwargs):
        self._init(**kwargs)

    def set_task(self, task: 'Task'):
        self._task = task

    def get_task(self) -> 'Task':
        return self._task

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

    @engine.setter
    def engine(self, engine: str):
        self._engine = engine

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
    def swarm(self):
        return self._swarm

    @swarm.setter
    def swarm(self, swarm: 'Swarm'):
        self._swarm = swarm

    @property
    def event_manager(self):
        return self._event_manager

    @event_manager.setter
    def event_manager(self, event_manager: 'EventManager'):
        self._event_manager = event_manager

    def deep_copy(self) -> 'Context':
        """Create a deep copy of this Context instance with all attributes copied.
        
        Returns:
            Context: A new Context instance with deeply copied attributes
        """
        # Create a new Context instance without calling __init__ to avoid singleton issues
        new_context = object.__new__(Context)

        # Manually copy all important instance attributes
        # Basic attributes
        new_context._user = self._user
        new_context._task_id = self._task_id
        new_context._engine = self._engine
        new_context._trace_id = self._trace_id

        # Session - shallow copy to maintain reference
        new_context._session = self._session

        # Task - set to None to avoid circular references
        new_context._task = None

        # Deep copy complex state objects
        try:
            new_context.context_info = copy.deepcopy(self.context_info)
        except Exception:
            new_context.context_info = copy.copy(self.context_info)

        try:
            # Use standard deep copy and then convert to ConfigDict if needed
            new_context.agent_info = copy.deepcopy(self.agent_info)
            # If the result is not ConfigDict but original was, convert it
            if isinstance(self.agent_info, ConfigDict) and not isinstance(new_context.agent_info, ConfigDict):
                new_context.agent_info = ConfigDict(new_context.agent_info)
        except Exception:
            # Fallback: manual deep copy for ConfigDict
            if isinstance(self.agent_info, ConfigDict):
                import json
                # Use JSON serialization for deep copy (if data is JSON-serializable)
                try:
                    serialized = json.dumps(dict(self.agent_info))
                    deserialized = json.loads(serialized)
                    new_context.agent_info = ConfigDict(deserialized)
                except Exception:
                    # Final fallback to shallow copy
                    new_context.agent_info = copy.copy(self.agent_info)
            else:
                new_context.agent_info = copy.copy(self.agent_info)

        try:
            new_context.trajectories = copy.deepcopy(self.trajectories)
        except Exception:
            new_context.trajectories = copy.copy(self.trajectories)

        try:
            new_context._token_usage = copy.deepcopy(self._token_usage)
        except Exception:
            new_context._token_usage = copy.copy(self._token_usage)

        # Copy other attributes if they exist
        if hasattr(self, '_swarm'):
            new_context._swarm = self._swarm  # Shallow copy for complex objects
        if hasattr(self, '_event_manager'):
            new_context._event_manager = self._event_manager  # Shallow copy for complex objects

        return new_context

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

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    def set_state(self, key: str, value: Any):
        self.state[key] = value


@dataclass
class AgentContext:
    """Agent context containing both configuration and runtime state.

    AgentContext is the core context management class in the AWorld architecture, used to store and manage
    the complete state information of an Agent, including configuration data and runtime state. Its main functions are:

    1. **State Restoration**: Save all state information during Agent execution, supporting Agent state restoration and recovery
    2. **Configuration Management**: Store Agent's immutable configuration information (such as agent_id, system_prompt, etc.)
    3. **Runtime State Tracking**: Manage Agent's mutable state during execution (such as messages, step, tools, etc.)
    4. **LLM Prompt Management**: Manage and maintain the complete prompt context required for LLM calls, including system prompts, historical messages, etc.
    5. **LLM Call Intervention**: Provide complete control over the LLM call process through Hook and ContextProcessor

    ## Lifecycle
    The lifecycle of AgentContext is completely consistent with the Agent instance:
    - **Creation**: Created during Agent initialization, containing initial configuration
    - **Runtime**: Continuously update runtime state during Agent execution
    - **Destruction**: Destroyed along with Agent instance destruction
    ```
    ┌─────────────────────── AWorld Runner ─────────────────────────┐
    |  ┌──────────────────── Agent Execution ────────────────────┐  │
    │  │  ┌────────────── Step 1 ─────────────┐ ┌── Step 2 ──┐   │  │
    │  │  │  [LLM Call]     [Tool Call(s)]    │
    │  │  │  [       Context Update      ]    │
    ```

    ## Field Classification
    - **Immutable Configuration Fields**: agent_id, agent_name, agent_desc, system_prompt, 
      agent_prompt, tool_names, context_rule
    - **Mutable Runtime Fields**: tools, step, messages, context_usage, llm_output

    ## LLM Call Intervention Mechanism
    AgentContext implements complete control over LLM calls through the following mechanisms:

    1. **Hook System**:
       - pre_llm_call_hook: Context preprocessing before LLM call
       - post_llm_call_hook: Result post-processing after LLM call
       - pre_tool_call_hook: Context adjustment before tool call
       - post_tool_call_hook: State update after tool call

    2. **PromptProcessor**:
       - Prompt Optimization: Optimize prompt content based on context length limitations
       - Message Compression: Intelligently compress historical messages to fit model context window
       - Context Rules: Apply context_rule for customized context processing

    ## Usage Scenarios
    1. **Agent Initialization**: Create AgentContext containing configuration information
    2. **LLM Call Control**: Pass as info parameter in policy(), async_policy() methods to control LLM behavior
    3. **Hook Callbacks**: Access and modify LLM call context in various Hooks, use PromptProcessor for prompt optimization and context processing
    4. **State Recovery**: Recover Agent's complete state from persistent storage
    """

    # ===== Immutable Configuration Fields =====
    agent_info: 'BaseAgent' = None
    context_rule: ContextRuleConfig = None
    _context: Context = None
    context_info: ContextState = None

    # ===== Mutable Configuration Fields =====
    tools: List[str] = None
    step: int = 0
    messages: List[Dict[str, Any]] = None
    context_usage: ContextUsage = None
    llm_output: ModelResponse = None

    def __init__(self,
                 agent_info: 'BaseAgent' = None,
                 context_rule: ContextRuleConfig = None,
                 tools: List[str] = None,
                 step: int = 0,
                 messages: List[Dict[str, Any]] = None,
                 context_usage: ContextUsage = None,
                 llm_output: ModelResponse = None,
                 context: Context = None,
                 parent_state: ContextState = None,
                 **kwargs):
        # Configuration fields
        self.agent_info = agent_info
        self.context_rule = context_rule

        # Runtime state fields
        self.tools = tools if tools is not None else []
        self.step = step
        self.messages = messages if messages is not None else []
        self.context_usage = context_usage if context_usage is not None else ContextUsage()
        self.llm_output = llm_output

        # Initialize Context with runner(session) level context
        self._context = context
        # Initialize ContextState with parent state (Context's state)
        # If context_state is provided, use it as parent; otherwise will be set later
        self.state = ContextState(parent_state=parent_state)

        # Additional fields for backward compatibility
        self._init(**kwargs)

    def _init(self, **kwargs):
        self._task_id = kwargs.get('task_id')

    def set_agent_info(self, agent_info: 'BaseAgent'):
        self.agent_info = agent_info

    def set_messages(self, messages: List[Dict[str, Any]]):
        self.messages = messages

    def set_tools(self, tools: List[str]):
        self.tools = tools

    def set_llm_output(self, llm_output: ModelResponse):
        self.llm_output = llm_output

    def increment_step(self) -> int:
        self.step += 1
        return self.step

    def set_step(self, step: int):
        self.step = step

    def get_step(self) -> int:
        return self.step

    def update_context_usage(self, used_context_length: int = None, total_context_length: int = None):
        if used_context_length is not None:
            self.context_usage.used_context_length = used_context_length
        if total_context_length is not None:
            self.context_usage.total_context_length = total_context_length

    def get_context_usage_ratio(self) -> float:
        """Get context usage ratio"""
        if self.context_usage.total_context_length <= 0:
            return 0.0
        return self.context_usage.used_context_length / self.context_usage.total_context_length

    def set_parent_state(self, parent_state: ContextState):
        self.state._parent_state = parent_state

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    def set_state(self, key: str, value: Any):
        self.state[key] = value

    def get_task(self) -> 'Task':
        return self._context.get_task()

    def get_session(self) -> Session:
        return self._context.session

    def get_engine(self) -> str:
        return self._context.engine

    def get_user(self) -> str:
        return self._context.user
