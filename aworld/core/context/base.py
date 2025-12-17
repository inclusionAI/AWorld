# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import copy
import time
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, Any, TYPE_CHECKING, List, Literal, Optional

from aworld.checkpoint.inmemory import InMemoryCheckpointRepository
from aworld.config import ConfigDict, AgentMemoryConfig
from aworld.core.context.context_state import ContextState
from aworld.core.context.session import Session
from aworld.logs.util import logger
from aworld.utils.common import nest_dict_counter

if TYPE_CHECKING:
    from aworld.core.task import Task, TaskResponse, TaskStatus, TaskStatusValue
    from aworld.events.manager import EventManager
    from aworld.core.agent import BaseAgent
    from aworld.core.context.amni import AgentContextConfig


@dataclass
class ContextUsage:
    total_context_length: int = 128000
    used_context_length: int = 0

    def __init__(self, total_context_length: int = 128000, used_context_length: int = 0):
        self.total_context_length = total_context_length
        self.used_context_length = used_context_length


@dataclass
class AgentTokenIdStep:
    step: int
    tool_call_ids: List[str] = field(default_factory=list)
    # Prompt token ids of the current llm call, including historical messages.
    prompt_token_ids: List[int] = field(default_factory=list)
    # Input token ids of the step, without tokens of previous steps.
    input_token_ids: List[int] = field(default_factory=list)
    output_token_ids: List[int] = field(default_factory=list)
    output_logprobs: List[float] = field(default_factory=list)
    output_versions: List[int] = field(default_factory=list)
    tool_resp_token_ids: List[int] = field(default_factory=list)
    finish_reason: Literal["length", "stop", "interrupt"] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert the AgentTokenIdStep to a dictionary."""
        return asdict(self)


@dataclass
class AgentTokenIdTrajectory:
    agent_id: str
    tool_call_id: str = None
    all_token_id_seq: List[int] = field(default_factory=list)
    token_id_steps: List[AgentTokenIdStep] = field(default_factory=list)

    def new_step(self):
        """Add a new step to the trajectory."""
        current_step = self.get_current_step()
        step = AgentTokenIdStep(step=(current_step.step if current_step else 0) + 1)
        self.token_id_steps.append(step)

    def get_current_step(self) -> AgentTokenIdStep:
        """Get the current step of the trajectory."""
        return self.token_id_steps[-1] if self.token_id_steps else None

    def to_dict(self) -> Dict[str, Any]:
        """Convert the AgentTokenIdTrajectory to a dictionary."""
        return asdict(self)


class Context:
    """Context is the core context management class in the AWorld architecture, used to store and manage
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
       tool_names, context_rule
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

    def __init__(self,
                 user: str = None,
                 task_id: str = None,
                 trace_id: str = None,
                 session: Session = None,
                 **kwargs):
        self._user = user
        self._init(task_id=task_id, trace_id=trace_id,
                   session=session, **kwargs)

    def _init(self, *, task_id: str = None, trace_id: str = None, session: Session = None, **kwargs):
        self._task_id = task_id
        self._task = None
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
        self._event_manager = None
        # checkpoint repository for saving/restoring context state
        self._checkpoint_repository = kwargs.get('checkpoint_repository', InMemoryCheckpointRepository())
        self._start = time.time()
        # agent_id -> token_id trajectory
        self._agent_token_id_traj: Dict[str, List[AgentTokenIdTrajectory]] = {}

        self._task_graph: Dict[str, Dict[str, Any]] = {}
        self.trajectory_dataset = None

    @property
    def start_time(self) -> float:
        return self._start

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
        return self._task.swarm

    @property
    def event_manager(self):
        return self._event_manager

    @event_manager.setter
    def event_manager(self, event_manager: 'EventManager'):
        self._event_manager = event_manager

    @property
    def checkpoint_repository(self):
        """Get checkpoint repository.

        Returns:
            The checkpoint repository if set, otherwise None
        """
        return self._checkpoint_repository

    @checkpoint_repository.setter
    def checkpoint_repository(self, repository: 'BaseCheckpointRepository'):
        """Set checkpoint repository.

        Args:
            repository: BaseCheckpointRepository instance for checkpoint storage
        """
        self._checkpoint_repository = repository

    @property
    def task_input(self):
        return self._task.input

    @task_input.setter
    def task_input(self, task_input):
        if self._task:
            self._task.input = task_input

    @property
    def outputs(self):
        return self._task.outputs

    @property
    def task_graph(self):
        return self._task_graph

    @task_graph.setter
    def task_graph(self, task_graph):
        self._task_graph = task_graph

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.context_info.get(key, default)

    def set_state(self, key: str, value: Any):
        self.context_info[key] = value

    async def build_sub_context(self, sub_task_content: Any, sub_task_id: str = None, **kwargs):
        # Create a new Context instance without calling __init__ to avoid singleton issues
        new_context = object.__new__(Context)
        self._deep_copy(new_context)
        new_context.task_id = sub_task_id
        new_context.task_input = sub_task_content
        self.add_task_node(self.agent_info, sub_task_id, self.task_id, **kwargs)
        return new_context

    def merge_sub_context(self, sub_task_context: 'ApplicationContext', **kwargs):
        self.merge_context(sub_task_context)

    def deep_copy(self) -> 'Context':
        # Create a new Context instance without calling __init__ to avoid singleton issues
        new_context = object.__new__(Context)
        return self._deep_copy(new_context)

    def _deep_copy(self, new_context) -> 'Context':
        """Create a deep copy of this Context instance with all attributes copied.

        Returns:
            Context: A new Context instance with deeply copied attributes
        """

        # Manually copy all important instance attributes
        # Basic attributes
        new_context._user = self._user
        new_context._task_id = self._task_id
        new_context._trace_id = self._trace_id
        new_context._start = self._start
        # Session - shallow copy to maintain reference
        new_context._session = self._session

        # Task - set to None to avoid circular references
        new_context._task = None

        new_context._task_graph = self._task_graph
        new_context.trajectory_dataset = self.trajectory_dataset

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
        if hasattr(self, '_event_manager'):
            new_context._event_manager = self._event_manager  # Shallow copy for complex objects

        if hasattr(self, '_agent_token_id_traj'):
            try:
                new_context._agent_token_id_traj = copy.deepcopy(self._agent_token_id_traj)
            except Exception:
                new_context._agent_token_id_traj = copy.copy(self._agent_token_id_traj)

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

        # 3. Merge token usage statistics
        if hasattr(other_context, '_token_usage') and other_context._token_usage:
            try:
                # Calculate net token usage increment from child context (avoid double counting tokens inherited from parent context)
                # If child context was created through deep_copy, it already contains parent context's tokens
                # We need to calculate the net increment
                parent_tokens = self._token_usage.copy()
                child_tokens = other_context._token_usage.copy()

                # Calculate net increment: child context tokens - parent context tokens
                net_tokens = {}
                for key in child_tokens:
                    child_value = child_tokens.get(key, 0)
                    parent_value = parent_tokens.get(key, 0)
                    net_value = child_value - parent_value
                    if net_value > 0:  # Only merge net increment
                        net_tokens[key] = net_value

                # Add net increment to parent context
                if net_tokens:
                    self.add_token(net_tokens)
            except Exception as e:
                logger.warning(f"Failed to merge token usage: {e}")
                # If calculating net increment fails, directly add child context's tokens (may result in double counting)
                try:
                    self.add_token(other_context._token_usage)
                except Exception:
                    pass

        # 4. Merge agent_info configuration (only merge new configuration items)
        if hasattr(other_context, 'agent_info') and other_context.agent_info:
            try:
                # Only merge configuration items that don't exist in parent context
                for key, value in other_context.agent_info.items():
                    if key not in self.agent_info:
                        self.agent_info[key] = value
            except Exception as e:
                logger.warning(f"Failed to merge agent_info: {e}")

        # Record merge operation
        try:
            merge_info = {
                "merged_at": datetime.now().isoformat(),
                "merged_from_task_id": getattr(other_context, '_task_id', 'unknown'),
                "merged_trajectories_count": len(other_context.trajectories) if hasattr(other_context,
                                                                                        'trajectories') else 0,
                "merged_token_usage": other_context._token_usage if hasattr(other_context, '_token_usage') else {},
            }
            self.context_info.set('last_merge_info', merge_info)
        except Exception as e:
            logger.warning(f"Failed to record merge info: {e}")

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

    async def update_task_after_run(self, task_response: 'TaskResponse'):
        pass

    def update_agent_step(self, agent_id: str):
        self.agent_info.current_agent_id = agent_id
        if agent_id not in self.agent_info:
            self.agent_info[agent_id] = {}
        if self.task_id not in self.agent_info[agent_id]:
            self.agent_info[agent_id][self.task_id] = {}
        agent_task_info = self.agent_info[agent_id][self.task_id]
        agent_task_info['step'] = agent_task_info.get('step', 0) + 1

    def get_agent_step(self, agent_info: dict, agent_id: str, task_id: str = None):
        if not agent_info:
            agent_info = self.agent_info
        if not task_id:
            task_id = self.task_id
        if not agent_id or not agent_info.get(agent_id, {}).get(task_id):
            return 0
        return agent_info[agent_id][task_id].get('step', 0)

    """
    Agent Skills Support
    """
    async def init_skill_list(self, skill_list: Dict[str, Any], namespace: str):
        """
        init skill list from agent
        """

    async def active_skill(self, skill_name: str, namespace: str) -> str:
        """
        activate a skill help agent to perform a task
        """
        pass

    async def offload_skill(self, skill_name: str, namespace: str) -> str:
        """
        offload a skill help agent to perform a task
        """
        pass

    async def get_active_skills(self, namespace: str) -> list[str]:
        """
        get skills from context
        """
        pass

    async def get_skill_list(self, namespace: str) -> Dict[str, Any]:
        pass

    def get_agent_token_id_traj(self, agent_id: str = None, tool_call_id: str = None) -> AgentTokenIdTrajectory:
        """Get the token id trajectory of the agent.

        Args:
            agent_id: Agent id.
            tool_call_id: Tool call id when agent as tool.

        Returns:
            AgentTokenIdTrajectory: Token id trajectory of the agent.
        """
        if not agent_id and 'current_agent_id' in self.agent_info:
            agent_id = self.agent_info.current_agent_id
        if not tool_call_id and 'current_tool_call_id' in self.agent_info:
            tool_call_id = self.agent_info.current_tool_call_id
        if not agent_id:
            logger.error("No current agent id found in context.")
            raise Exception("No current agent id found in context.")

        if agent_id not in self._agent_token_id_traj:
            self._agent_token_id_traj[agent_id] = []
        trajectories = self._agent_token_id_traj[agent_id]
        if tool_call_id:
            for traj in trajectories:
                if traj.tool_call_id == tool_call_id:
                    return traj
                traj = AgentTokenIdTrajectory(agent_id=agent_id, tool_call_id=tool_call_id)
                trajectories.append(traj)
                return traj
        else:
            if trajectories:
                return trajectories[0]
            else:
                traj = AgentTokenIdTrajectory(agent_id=agent_id, tool_call_id=tool_call_id)
                trajectories.append(traj)
                return traj

    def add_llm_resp_token_ids(self,
                               input_token_ids: List[int],
                               prompt_token_ids: List[int],
                               response: "TokenIdModelResponse",
                               agent_id: str = None,
                               tool_call_id: str = None):
        """Add the token ids of the current step input to the context.

        Args:
            agent_id: Agent id.
            input_token_ids: Input token ids of the current step.
            prompt_token_ids: Prompt token ids of the current llm call.
            response: Token id model response.
            tool_call_id: Tool call id when agent as tool.
        """
        token_id_traj = self.get_agent_token_id_traj(agent_id, tool_call_id)
        step = token_id_traj.get_current_step()
        if not step:
            logger.error(f"No current step found in context. agent_id: {agent_id}, tool_call_id: {tool_call_id}")
            raise Exception("No current step found in context.")

        step.prompt_token_ids = prompt_token_ids
        step.input_token_ids = input_token_ids
        step.output_token_ids = response.output_token_ids
        step.output_logprobs = response.output_logprobs
        step.output_versions = response.output_versions
        step.finish_reason = response.finish_reason
        token_id_traj.all_token_id_seq.extend(step.input_token_ids + step.output_token_ids)

    def add_tool_resp_token_ids(self,
                                tool_resp_token_ids: List[int],
                                resp_tool_call_ids: List[str],
                                agent_id: str = None,
                                tool_call_id: str = None):
        """Add the token ids of the current step tool response to the context.

        Args:
            agent_id: Agent id.
            tool_resp_token_ids: Tool response token ids of the current step.
            tool_call_id: Tool call id when agent as tool.
        """
        if not tool_resp_token_ids:
            return
        token_id_traj = self.get_agent_token_id_traj(agent_id, tool_call_id)
        step = token_id_traj.get_current_step()
        if not step:
            logger.error("No current step found in context.")
            raise Exception("No current step found in context.")
        step.tool_call_ids = resp_tool_call_ids
        step.tool_resp_token_ids = tool_resp_token_ids
        step.output_token_ids.extend(tool_resp_token_ids)
        step.output_logprobs.extend([0.0] * len(tool_resp_token_ids))
        step.output_versions.extend([-1] * len(tool_resp_token_ids))
        token_id_traj.all_token_id_seq.extend(step.tool_resp_token_ids)

    def new_trajectory_step(self, agent_id: str = None, tool_call_id: str = None):
        """Add a new trajectory step to the context.

        Args:
            agent_id: Agent id.
        """
        token_id_traj = self.get_agent_token_id_traj(agent_id, tool_call_id)
        token_id_traj.new_step()

    def get_current_step_of_trajectory(self, agent_id: str = None, tool_call_id: str = None) -> AgentTokenIdStep:
        """Get the current step of the trajectory.

        Args:
            agent_id: Agent id.
            tool_call_id: Tool call id when agent as tool.

        Returns:
            AgentTokenIdStep: Current step of the trajectory.
        """
        token_id_traj = self.get_agent_token_id_traj(agent_id, tool_call_id)
        return token_id_traj.get_current_step()

    def merge_sub_task_token_ids(self, sub_task_context: 'Context'):
        """Merge sub task token ids to context"""
        for agent_id, token_id_trajs in sub_task_context._agent_token_id_traj.items():
            for traj in token_id_trajs:
                self._agent_token_id_traj[agent_id].append(traj)


    """
        Context Checkpoint Support
    """
    def _create_checkpoint_values(self) -> Dict[str, Any]:
        """Extract key state information from context for checkpoint.

        Returns:
            Dict containing context state values for checkpoint.
        """
        return {
            # Context state information
            'context_info': self.context_info.to_dict() if self.context_info else {},

            # Agent configuration
            'agent_info': dict(self.agent_info) if self.agent_info else {},

            # Execution trajectories
            'trajectories': dict(self.trajectories) if self.trajectories else {},

            # Token usage statistics
            'token_usage': copy.deepcopy(self._token_usage) if self._token_usage else {},

            # Basic identifiers
            'user': self._user,
            'task_id': self._task_id,
            'trace_id': self._trace_id,

            # Timestamp for checkpoint creation
            'checkpoint_created_at': datetime.now().isoformat(),
        }

    def _create_checkpoint_metadata(self, metadata_extra: Optional[Dict[str, Any]] = None) -> 'CheckpointMetadata':
        """Create checkpoint metadata.

        Args:
            metadata_extra: Extra metadata to include.

        Returns:
            CheckpointMetadata object.
        """
        from aworld.checkpoint import CheckpointMetadata

        metadata_dict = {
            'session_id': self.session_id or 'unknown',
            'task_id': self._task_id or 'unknown',
        }

        # Add extra metadata if provided
        if metadata_extra:
            metadata_dict.update(metadata_extra)

        return CheckpointMetadata(**metadata_dict)

    async def snapshot(self):
        """Save current context state to a checkpoint.

        This method serializes the current context state into a Checkpoint object,
        which will be automatically saved to the internal checkpoint_repository
        if one has been set via `context.checkpoint_repository = repo`.
        """
        from aworld.checkpoint import create_checkpoint, VersionUtils

        # Extract checkpoint values
        checkpoint_values = self._create_checkpoint_values()

        # Create checkpoint metadata
        from aworld.checkpoint import CheckpointMetadata

        checkpoint_metadata = CheckpointMetadata(
            session_id=self.session_id,
            task_id=self._task_id
        )

        # Get version for the checkpoint
        version = 1
        if self._checkpoint_repository:
            try:
                # Try to get last checkpoint for this session to determine next version
                last_checkpoint = await self._checkpoint_repository.aget_by_session(self.session_id)
                if last_checkpoint:
                    version = VersionUtils.get_next_version(last_checkpoint.version)
            except Exception as e:
                logger.warning(f"Failed to get last checkpoint version: {e}")

        # Create the checkpoint
        checkpoint = create_checkpoint(
            values=checkpoint_values,
            metadata=checkpoint_metadata,
            version=version
        )

        # Save asynchronously if repository available
        if self._checkpoint_repository:
            try:
                await self._checkpoint_repository.aput(checkpoint)
                logger.info(f"Checkpoint {checkpoint.id} saved asynchronously for task {self._task_id}")
            except Exception as e:
                logger.error(f"Failed to save checkpoint asynchronously: {e}")

        return checkpoint

    async def get_task_status(self):
        from aworld.core.common import TaskStatusValue
        return TaskStatusValue.SUCCESS

    async def update_task_status(self, task_id: str, status: 'TaskStatus'):
        pass

    async def post_init(self):
        pass

    def get_agent_context_config(self, namespace: str) -> 'AgentContextConfig':
        pass

    def get_agent_memory_config(self, namespace: str) -> 'AgentMemoryConfig':
        pass



    """
        Sub Task Trajectory Support
    """

    async def add_task_trajectory(self, task_id: str, task_trajectory: List[Dict[str, Any]]):
        """Add trajectory data for a task.

        Args:
            task_id: The task id.
            task_trajectory: The list of trajectory steps.
        """
        if self.trajectory_dataset is not None:
            await self.trajectory_dataset.save_task_trajectory(task_id, task_trajectory)


    async def update_task_trajectory(self, message: Any, task_id: str = None, **kwargs):
        """
        Generate trajectory item from message (or other source) and append to dataset.

        Args:
            message: Source message or data
            task_id: Optional task id
        """
        if not task_id:
            logger.error("update_task_trajectory#task_id is required")
            raise Exception("update_task_trajectory#task_id is required")

        if self.trajectory_dataset is not None:
            item = await self.trajectory_dataset.append_trajectory(message, task_id=task_id)

    async def get_task_trajectory(self, task_id: str) -> List['TrajectoryItem']:
        """Get trajectory data for a task.

        Args:
            task_id: The task id.

        Returns:
            List[Dict[str, Any]]: The list of trajectory steps.
        """
        # Try to get from storage first
        if self.trajectory_dataset is not None:
            trajectory = await self.trajectory_dataset.get_task_trajectory(task_id)
            return trajectory

    def add_task_node(self, caller_agent_info: dict, child_task_id: str, parent_task_id: str, **kwargs):
        """Add a task node and its relationship to the task graph.

        Args:
            child_task_id: Child task id.
            parent_task_id: Parent task id.
        """
        if child_task_id not in self._task_graph:
            self._task_graph[child_task_id] = {}
        child_task_node = self._task_graph[child_task_id]

        agent_info = caller_agent_info
        if not agent_info:
            agent_info = self.agent_info
        caller_id = agent_info.current_agent_id if agent_info and hasattr(agent_info, 'current_agent_id') else None
        caller_info = child_task_node.get("caller_info", {})
        caller_info.update({
            "agent_id": caller_id,
            "agent_step": self.get_agent_step(agent_info, caller_id, parent_task_id)
        })

        self._task_graph[child_task_id].update({
            "parent_task": parent_task_id,
            "caller_info": caller_info,
            **kwargs
        })
        logger.info(f"{self.task_id}#Task graph: {self._task_graph}")

    def get_task_graph(self) -> Dict[str, Any]:
        """Get the task execution graph structure.

        Returns:
            Dict containing nodes and edges representing the task execution flow.
            Format:
            {
                "nodes": [{"id": "task_id", "data": {...}}],
                "edges": [{"source": "parent_id", "target": "child_id", "relation": "..."}]
            }
        """
        nodes = []
        edges = []

        # Collect all unique task IDs
        task_ids = set(self._task_graph.keys())
        for child_data in self._task_graph.values():
            if "parent_task" in child_data and child_data["parent_task"] is not None:
                task_ids.add(child_data["parent_task"])

        # Build nodes
        for tid in task_ids:
            nodes.append({"id": tid})

        # Build edges
        for child_id, data in self._task_graph.items():
            parent_id = data.get("parent_task")
            if parent_id:
                edges.append({
                    "source": parent_id,
                    "target": child_id,
                    "metadata": data
                })

        return {
            "nodes": nodes,
            "edges": edges
        }
