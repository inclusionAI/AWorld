# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import copy
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, TYPE_CHECKING, Optional

from aworld.config import ConfigDict
from aworld.core.context.context_state import ContextState
from aworld.core.context.session import Session
from aworld.logs.util import logger
from aworld.utils.common import nest_dict_counter
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from aworld.core.task import Task
    from aworld.core.agent.swarm import Swarm
    from aworld.events.manager import EventManager
    from aworld.core.agent import BaseAgent


@dataclass
class ContextUsage:
    total_context_length: int = 128000
    used_context_length: int = 0

    def __init__(self, total_context_length: int = 128000, used_context_length: int = 0):
        self.total_context_length = total_context_length
        self.used_context_length = used_context_length


class Context(BaseModel):
    """
    Context is the core context management class in the AWorld architecture, used to store and manage
    the complete state information of an Agent, including configuration data and runtime state.

    Context serves as both a session-level context manager and agent-level context manager, providing:

    1. **State Restoration**: Save all state information during Agent execution, supporting Agent state restoration and recovery
    2. **Configuration Management**: Store Agent's immutable configuration information (such as agent_id, system_prompt, etc.)
    3. **Runtime State Tracking**: Manage Agent's mutable state during execution (such as messages, step, tools, etc.)
    4. **LLM Prompt Management**: Manage and maintain the complete prompt context required for LLM calls, including system prompts, historical messages, etc.
    5. **LLM Call Intervention**: Provide complete control over the LLM call process through Hook and ContextProcessor
    6. **Multi-task State Management**: Support fork_new_task and context merging for complex multi-task scenarios

    ## Lifecycle
    The lifecycle of Context is completely consistent with the Agent instance:
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
    - **Mutable Runtime Fields**: tools, step, messages, context_usage, llm_output, trajectories

    ## LLM Call Intervention Mechanism
    Context implements complete control over LLM calls through the following mechanisms:

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
    1. **Agent Initialization**: Create Context containing configuration information
    2. **LLM Call Control**: Pass as info parameter in policy(), async_policy() methods to control LLM behavior
    3. **Hook Callbacks**: Access and modify LLM call context in various Hooks, use PromptProcessor for prompt optimization and context processing
    4. **State Recovery**: Recover Agent's complete state from persistent storage
    5. **Multi-task Management**: Use fork_new_task to create child contexts and merge_context to consolidate results

    Examples:
        >>> context = Context()
        >>> context.set_state("key", "value")
        >>> child_context = context.deep_copy()
        >>> context.merge_context(child_context)
    """
    user: Optional[str] = None
    task_id: Optional[str] = None
    trace_id: Optional[str] = None
    session: Optional[Session] = None
    engine: Optional[str] = None
    context_info: Any = Field(default_factory=lambda: ContextState())
    agent_info: Dict[str, Any] = Field(default_factory=ConfigDict)
    trajectories: Any = Field(default_factory=OrderedDict)
    token_usage: Dict[str, int] = Field(default_factory=lambda: {
        "completion_tokens": 0,
        "prompt_tokens": 0,
        "total_tokens": 0,
    })
    swarm: Any = None
    event_manager: Any = None
    _task: Optional['Task'] = None

    model_config = {
        "arbitrary_types_allowed": True
    }

    def add_token(self, usage: Dict[str, int]):
        self.token_usage = nest_dict_counter(self.token_usage, usage)

    def reset(self, **kwargs):
        # Reset fields based on kwargs
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def set_task(self, task: 'Task'):
        self._task = task

    def get_task(self) -> 'Task':
        return self._task

    @property
    def session_id(self):
        """Get session ID from session object"""
        if self.session:
            return self.session.session_id
        return None

    @session_id.setter
    def session_id(self, value: str):
        self.session.session_id = value

    @property
    def task_input(self):
        """Get task input from task object"""
        if self._task:
            return self._task.input
        return None

    @property
    def outputs(self):
        """Get outputs from task object"""
        if self._task:
            return self._task.outputs
        return None

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.context_info.get(key, default)

    def set_state(self, key: str, value: Any):
        self.context_info[key] = value

    def deep_copy(self) -> 'Context':
        """Create a deep copy of this Context instance with all attributes copied.

        Returns:
            Context: A new Context instance with deeply copied attributes
        """
        # Create a new Context instance using Pydantic's proper initialization
        new_context = Context()

        # Manually copy all important instance attributes
        # Basic attributes
        new_context.user = self.user
        new_context.task_id = self.task_id
        new_context.engine = self.engine
        new_context.trace_id = self.trace_id

        # Session - shallow copy to maintain reference
        new_context.session = self.session

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
            new_context.token_usage = copy.deepcopy(self.token_usage)
        except Exception:
            new_context.token_usage = copy.copy(self.token_usage)

        # Copy other attributes if they exist
        if hasattr(self, 'swarm'):
            new_context.swarm = self.swarm  # Shallow copy for complex objects
        if hasattr(self, 'event_manager'):
            new_context.event_manager = self.event_manager  # Shallow copy for complex objects

        return new_context

    def merge_context(self, other_context: 'Context') -> None:
        if not other_context:
            return

        # 1. Merge context_info state
        if hasattr(other_context, 'context_info') and other_context.context_info:
            try:
                # Get local state from child context (excluding inherited parent state)
                if hasattr(other_context.context_info, 'local_dict'):
                    local_state = other_context.context_info.local_dict()
                    if local_state:
                        self.context_info.update(local_state)
                else:
                    # If no local_dict method, directly update all states
                    self.context_info.update(other_context.context_info)
            except Exception as e:
                logger.warning(f"Failed to merge context_info: {e}")

        # 2. Merge trajectories
        if hasattr(other_context, 'trajectories') and other_context.trajectories:
            try:
                # Use timestamp or step number to avoid key conflicts
                for key, value in other_context.trajectories.items():
                    # If key already exists, add suffix to avoid overwriting
                    merge_key = key
                    counter = 1
                    while merge_key in self.trajectories:
                        merge_key = f"{key}_merged_{counter}"
                        counter += 1
                    self.trajectories[merge_key] = value
            except Exception as e:
                logger.warning(f"Failed to merge trajectories: {e}")

    def save_action_trajectory(self,
                               step,
                               result: str,
                               agent_name: str = None,
                               tool_name: str = None,
                               params: str = None):
        step_key = f"step_{step}"
        step_data = {
            "step": step,
            "params": params,
            "result": result,
            "timestamp": datetime.now().isoformat(),
            "agent_name": agent_name,
            "tool_name": tool_name
        }
        self.trajectories[step_key] = step_data

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert Context to dictionary for serialization
        
        Returns:
            Dict containing all serializable Context attributes
        """
        result = {
            "user": self.user,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "engine": self.engine,
            "context_info": self.context_info.to_dict() if hasattr(self.context_info, 'to_dict') else self.context_info,
            "agent_info": dict(self.agent_info) if hasattr(self.agent_info, '__iter__') else self.agent_info,
            "trajectories": dict(self.trajectories) if hasattr(self.trajectories, '__iter__') else self.trajectories,
            "token_usage": self.token_usage,
            "swarm": None,  # Skip complex objects
            "event_manager": None,  # Skip complex objects
            "_task": None,  # Skip task reference to avoid circular references
        }
        
        # Add session info if available
        if self.session:
            result["session"] = {
                "session_id": self.session.session_id,
                "last_update_time": self.session.last_update_time,
                "trajectories": self.session.trajectories
            }
        
        return result


