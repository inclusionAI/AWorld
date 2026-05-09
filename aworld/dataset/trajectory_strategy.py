# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Trajectory generation strategy interface and default implementation.

This module provides an extensible strategy pattern for generating training trajectories
from task execution. Users can implement custom strategies by extending TrajectoryStrategy.
"""

import abc
import json
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from aworld.core.agent.base import AgentFactory
from aworld.core.common import Observation
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.dataset.types import (
    TrajectoryItem,
    TrajectoryState,
    TrajectoryAction,
    TrajectoryReward,
    ExpMeta,
)
from aworld.utils.serialized_util import to_serializable

if TYPE_CHECKING:
    from aworld.core.agent.swarm import Swarm
    from aworld.runners.task_runner import TaskRunner


class TrajectoryStrategy(abc.ABC):
    """Abstract base class for trajectory generation strategies.
    
    This class defines the interface for generating training trajectories from task execution.
    Custom strategies should inherit from this class and implement the generate method.
    
    Example:
        >>> class CustomTrajectoryStrategy(TrajectoryStrategy):
        ...     async def generate(self, task_id: str, event_runner: TaskRunner) -> List[Dict[str, Any]]:
        ...         # Custom implementation
        ...         messages = await event_runner.event_mng.messages_by_task_id(task_id)
        ...         # Process messages with custom logic
        ...         return custom_trajectory_data
    """
    
    @abc.abstractmethod
    async def generate(
        self, 
        task_id: str, 
        event_runner: 'TaskRunner'
    ) -> Optional[List[Dict[str, Any]]]:
        """Generate trajectory data for a given task.
        
        Args:
            task_id (str): The unique identifier of the task
            event_runner (TaskRunner): The task runner instance containing:
                - event_mng: EventManager for accessing task messages
                - state_manager: RuntimeStateManager for accessing execution state
                - context: Task execution context
                
        Returns:
            Optional[List[Dict[str, Any]]]: List of trajectory items in dictionary format,
                or None if generation fails.
        """
        pass

    @abc.abstractmethod
    async def build_trajectory_state(self, source: Any, **kwargs) -> Optional[TrajectoryState]:
        """Build TrajectoryState (SAR) from a source."""
        pass

    @abc.abstractmethod
    async def build_trajectory_action(self, source: Any, **kwargs) -> Optional[TrajectoryAction]:
        """Build TrajectoryAction (A) from a source."""
        pass

    @abc.abstractmethod
    async def build_trajectory_reward(self, source: Any, **kwargs) -> Optional[TrajectoryReward]:
        """Build TrajectoryReward (R) from a source."""
        pass

    @abc.abstractmethod
    async def generate_item(self, source: Any, **kwargs) -> Optional[TrajectoryItem]:
        """Generate a single trajectory item from source.
        
        Args:
            source (Any): Source data to generate trajectory item from (e.g. Message)
            **kwargs: Additional arguments, typically includes runtime states
            
        Returns:
            Optional[Dict[str, Any]]: Generated trajectory item or None
        """
        return None
    
    def validate_trajectory(self, trajectory: List[Dict[str, Any]]) -> bool:
        """Validate the generated trajectory data.
        
        This method can be overridden to add custom validation logic.
        
        Args:
            trajectory (List[Dict[str, Any]]): Generated trajectory data
            
        Returns:
            bool: True if trajectory is valid, False otherwise
        """
        return True


class DefaultTrajectoryStrategy(TrajectoryStrategy):
    """Default trajectory generation strategy using standardized SAR structure."""

    CACHE_USAGE_MESSAGE_KEYS = {
        "cache_hit_tokens",
        "cache_write_tokens",
        "prompt_tokens_details",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "input_tokens_details",
    }

    def __init__(self):
        # task_id + agent_id -> step counter
        self.task_agent_map: Dict[str, int] = {}

    async def generate(
        self, 
        task_id: str, 
        event_runner: 'TaskRunner'
    ) -> Optional[List[Dict[str, Any]]]:
        """Generate trajectory using SAR format."""
        try:
            logger.info(f"Generating trajectory for task {task_id} using default SAR strategy")
            messages = await event_runner.event_mng.messages_by_task_id(task_id)

            trajectories: List[Dict[str, Any]] = []
            for msg in messages:
                item = await self.message_to_trajectory_item(
                    msg,
                    state_manager=event_runner.state_manager,
                    use_tools_in_prompt=getattr(event_runner, "use_tools_in_prompt", True)
                )
                if item:
                    trajectories.append(item.to_dict())

            if trajectories and self.validate_trajectory(trajectories):
                logger.info(f"Successfully generated {len(trajectories)} trajectory items for task {task_id}")
                return trajectories

            logger.warning(f"Generated trajectory validation failed for task {task_id}")
            return None
        except Exception as e:
            logger.error(f"Failed to generate trajectory for task {task_id}: {str(e)}")
            return None

    async def generate_item(self, source: Any, **kwargs) -> Optional[TrajectoryItem]:
        """Generate a single trajectory item from source using default SAR logic."""
        from aworld.core.event.base import Message

        state_manager = kwargs.get('state_manager')
        use_tools_in_prompt = kwargs.get('use_tools_in_prompt', True)

        if isinstance(source, Message):
            try:
                item = await self.message_to_trajectory_item(
                    source,
                    state_manager=state_manager,
                    use_tools_in_prompt=use_tools_in_prompt
                )
                return item
                # return item.to_dict() if item else None
            except Exception as e:
                logger.warning(f"Failed to convert message to trajectory item: {e}")
                return None
        return None

    async def build_trajectory_state(self, source: Any, **kwargs) -> Optional[TrajectoryState]:
        """Build TrajectoryItem (SAR) from a source."""
        # State (S)
        history_messages = self._get_llm_messages_from_truth_source(source)
        if history_messages is None:
            history_messages = self._get_llm_messages_from_memory(source, kwargs.get("use_tools_in_prompt", False))
        ctx_obj = getattr(source, "context", None)
        ctx_dict = {}
        if ctx_obj and hasattr(ctx_obj, "context_info"):
            info = ctx_obj.context_info
            if hasattr(info, "to_dict"):
                try:
                    ctx_dict = info.to_dict()
                except Exception:
                    ctx_dict = {}
            elif isinstance(info, dict):
                ctx_dict = info
        input = source.payload
        if isinstance(source, Message):
            data = source.payload
            if isinstance(data, Observation):
                input = {
                    "from_agent_name": data.from_agent_name,
                    "to_agent_name": data.to_agent_name,
                    "content": data.content,
                    "action_result": data.action_result,
                    "image": data.image,
                    "images": data.images,
                    "dom_tree": to_serializable(data.dom_tree)
                }
        state = TrajectoryState(
            input=input,
            messages=history_messages,
            # context=ctx_dict
        )
        return state

    def _get_llm_messages_from_truth_source(self, message: Any) -> Optional[List[Dict[str, Any]]]:
        context = getattr(message, "context", None)
        if context is None or not hasattr(context, "context_info"):
            return None

        llm_calls = context.context_info.get("llm_calls")
        if isinstance(llm_calls, list) and llm_calls:
            llm_call = self._select_llm_call_for_message(message, llm_calls)
            if isinstance(llm_call, dict):
                request = llm_call.get("request")
                if isinstance(request, dict):
                    messages = request.get("messages")
                    if isinstance(messages, list):
                        return self._sanitize_trajectory_messages(messages)

        legacy_messages = context.context_info.get("llm_input")
        if isinstance(legacy_messages, list):
            return self._sanitize_trajectory_messages(to_serializable(legacy_messages))
        return None

    def _select_llm_call_for_message(self, message: Any, llm_calls: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        valid_calls = []
        for llm_call in llm_calls:
            if not isinstance(llm_call, dict):
                continue
            request = llm_call.get("request")
            if not isinstance(request, dict) or not isinstance(request.get("messages"), list):
                continue
            valid_calls.append(llm_call)

        if not valid_calls:
            return None

        message_task_id = getattr(message, "task_id", None)
        if message_task_id is not None:
            task_matches = [call for call in valid_calls if call.get("task_id") == message_task_id]
            if task_matches:
                valid_calls = task_matches
            elif any(call.get("task_id") is not None for call in valid_calls):
                return None

        agent_id = getattr(message, "receiver", None)
        if agent_id is not None:
            agent_matches = [call for call in valid_calls if call.get("agent_id") == agent_id]
            if agent_matches:
                valid_calls = agent_matches
            elif any(call.get("agent_id") is not None for call in valid_calls):
                return None

        message_timestamp = getattr(message, "timestamp", None)
        if isinstance(message_timestamp, (int, float)):
            prior_calls = [
                call for call in valid_calls
                if (call.get("finished_at") or call.get("started_at")) is not None
                and (call.get("finished_at") or call.get("started_at")) <= message_timestamp
            ]
            if prior_calls:
                return max(prior_calls, key=lambda call: call.get("finished_at") or call.get("started_at") or 0)
            if any((call.get("finished_at") or call.get("started_at")) is not None for call in valid_calls):
                return None

        return valid_calls[-1]

    def _sanitize_trajectory_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self._strip_cache_usage_fields(message) for message in messages]

    def _strip_cache_usage_fields(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._strip_cache_usage_fields(item)
                for key, item in value.items()
                if key not in self.CACHE_USAGE_MESSAGE_KEYS
            }
        if isinstance(value, list):
            return [self._strip_cache_usage_fields(item) for item in value]
        return value

    async def build_trajectory_action(self, source: Any, **kwargs) -> Optional[TrajectoryAction]:
        from aworld.core.event.base import Message
        if not isinstance(source, Message):
            return None
        from aworld.core.common import ActionModel
        from aworld.core.event.base import Message
        from aworld.utils.serialized_util import to_serializable
        state_manager = kwargs.get('state_manager')
        node = state_manager._find_node(source.id) if state_manager else None
        agent_results = []
        ext_info = {}
        if node and node.results:
            for handle_result in node.results:
                result = handle_result.result
                if isinstance(result, Message) and isinstance(result.payload, list):
                    agent_results.extend(result.payload)
                else:
                    if not ext_info.get("agent_results"):
                        ext_info["agent_results"] = []
                    ext_info["agent_results"].append(to_serializable(handle_result))

        def _get_attr_from_action(obj, attr, default=None):
            if isinstance(obj, ActionModel):
                return getattr(obj, attr, default)
            elif isinstance(obj, dict) and attr in obj:
                return obj[attr]
            return default

        action_content = None
        agent_name = None
        tool_calls: List[Dict[str, Any]] = []
        if agent_results:
            first_action = agent_results[0]
            action_content = _get_attr_from_action(first_action, "policy_info", None)
            agent_name = _get_attr_from_action(first_action, "agent_name")
            for action in agent_results:
                tool_call_id = _get_attr_from_action(action, "tool_call_id")
                if tool_call_id:
                    tool_calls.append({
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": _get_attr_from_action(action, "tool_name"),
                            "arguments": json.dumps(_get_attr_from_action(action, "params"), ensure_ascii=False),
                        }
                    })
        if not agent_name:
            agent_name = source.receiver
        agent = AgentFactory.agent_instance(agent_name)
        action = TrajectoryAction(
            content=action_content,
            tool_calls=tool_calls,
            is_agent_finished=agent._finished if agent else False,
        )
        return action

    async def build_trajectory_reward(self, source: Any, **kwargs) -> Optional[TrajectoryReward]:
        """Build TrajectoryItem (SAR) from a source."""
        return TrajectoryReward(tool_outputs=[], status=None, score=None)

    async def message_to_trajectory_item(
        self,
        message: Any,
        state_manager: Any = None,
        use_tools_in_prompt: bool = False
    ) -> Optional[TrajectoryItem]:
        """Build TrajectoryItem (SAR) from a message."""
        from aworld.core.common import ActionModel
        from aworld.core.event.base import Message
        from aworld.utils.serialized_util import to_serializable

        if not message:
            raise ValueError("Message cannot be empty")

        agent_id = message.receiver
        session_id = message.context.session_id
        task_id = message.context.task_id
        task_name = message.context.get_task().name
        pre_agent = message.sender
        task_agent_id = f"{task_id}_{agent_id}"

        if task_agent_id not in self.task_agent_map:
            self.task_agent_map[task_agent_id] = 0
        self.task_agent_map[task_agent_id] += 1

        step = message.context.get_agent_step(agent_id=agent_id, task_id=task_id, agent_info=message.context.agent_info)
        meta = ExpMeta(
            session_id=session_id,
            task_id=task_id,
            task_name=task_name,
            agent_id=agent_id,
            step=step,
            execute_time=message.timestamp,
            pre_agent=pre_agent
        )
        # State (S)
        state = await self.build_trajectory_state(message, state_manager=state_manager,
                                                  use_tools_in_prompt=use_tools_in_prompt)

        # Action (A)
        action = await self.build_trajectory_action(message, state_manager=state_manager,
                                                    use_tools_in_prompt=use_tools_in_prompt)

        # Reward(R)
        reward = await self.build_trajectory_reward(message, state_manager=state_manager,
                                                    use_tools_in_prompt=use_tools_in_prompt)

        return TrajectoryItem(
            id=self._build_trajectory_item_id(message),
            meta=meta,
            state=state,
            action=action,
            reward=reward
        )

    def _build_trajectory_item_id(self, message: Message) -> str:
        ctx_obj = getattr(message, "context", None)
        if ctx_obj and hasattr(ctx_obj, "context_info"):
            llm_calls = ctx_obj.context_info.get("llm_calls")
            if isinstance(llm_calls, list) and llm_calls:
                record = self._select_llm_call_for_message(message, llm_calls)
                if isinstance(record, dict):
                    call_id = record.get("call_id")
                    if isinstance(call_id, str) and call_id:
                        return f"{message.id}:{call_id}"
        return str(message.id)

    def _get_llm_messages_from_memory(self, message: Any, use_tools_in_prompt: bool):
        from aworld.memory.main import MemoryFactory
        from aworld.memory.models import MemoryMessage

        memory = MemoryFactory.instance()
        histories = memory.get_all(
            filters={
                "agent_id": message.receiver,
                "task_id": message.task_id,
                "memory_type": ["init", "message", "summary"]
            })
        messages = []
        if histories:
            for history in histories:
                if isinstance(history, MemoryMessage):
                    openai_msg = history.to_openai_message()
                    if history.role == "assistant":
                        ext_info = history.metadata.get("ext_info", {}) if history.metadata else {}
                        raw_response = ext_info.get("raw_response", "")
                        openai_msg["raw_response"] = raw_response
                    messages.append(openai_msg)
                else:
                    if not use_tools_in_prompt and history.metadata.get('tool_calls'):
                        messages.append({'role': history.metadata['role'], 'content': history.content,
                                         'tool_calls': [history.metadata['tool_calls']]})
                    else:
                        messages.append({'role': history.metadata['role'], 'content': history.content,
                                         "tool_call_id": history.metadata.get("tool_call_id")})
        return messages

    def validate_trajectory(self, trajectory: List[Dict[str, Any]]) -> bool:
        if not trajectory:
            return False
        for item in trajectory:
            if not isinstance(item, dict) or 'id' not in item:
                logger.warning(f"Invalid trajectory item: {item}")
                return False
            if not {'state', 'action', 'reward'} <= set(item.keys()):
                logger.warning(f"SAR fields missing in trajectory item: {item.keys()}")
                return False
        return True


class FilteredTrajectoryStrategy(TrajectoryStrategy):
    """
    Base class for filtered trajectory generation strategies.
    
    This is an abstract base class that provides a framework for filtering trajectory items.
    Subclasses should override filter methods to implement specific filtering logic.
    
    Methods to override:
        - filter_by_agent(agent_id: str) -> bool: Return True to keep the item
        - filter_by_step(step: int) -> bool: Return True to keep the item
        - filter_by_item(item: Dict) -> bool: Custom filtering logic
    
    Example:
        >>> class PlannerOnlyStrategy(FilteredTrajectoryStrategy):
        ...     def filter_by_agent(self, agent_id: str) -> bool:
        ...         return agent_id.startswith("planner_")
        ...     
        ...     def filter_by_step(self, step: int) -> bool:
        ...         return step <= 10
    """
    
    def filter_by_agent(self, agent_id: str) -> bool:
        """
        Override this method to filter by agent ID.
        
        Args:
            agent_id: The agent identifier
            
        Returns:
            bool: True to keep this item, False to filter it out
        """
        return True  # Default: keep all agents
    
    def filter_by_step(self, step: int) -> bool:
        """
        Override this method to filter by step number.
        
        Args:
            step: The step number
            
        Returns:
            bool: True to keep this item, False to filter it out
        """
        return True  # Default: keep all steps
    
    def filter_by_item(self, item: Dict[str, Any]) -> bool:
        """
        Override this method for custom filtering logic.
        
        Args:
            item: The trajectory item dictionary
            
        Returns:
            bool: True to keep this item, False to filter it out
        """
        return True  # Default: keep all items
    
    async def generate(
        self, 
        task_id: str, 
        event_runner: 'TaskRunner'
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Generate filtered trajectory data.
        
        This method generates a full trajectory and then applies the filter methods.
        
        Args:
            task_id (str): The unique identifier of the task
            event_runner (TaskRunner): The task runner instance
            
        Returns:
            Optional[List[Dict[str, Any]]]: Filtered trajectory data or None if failed
        """
        from aworld.dataset.trajectory_dataset import generate_trajectory
        
        try:
            logger.info(f"Generating filtered trajectory for task {task_id}")
            
            # Get base trajectory
            messages = await event_runner.event_mng.messages_by_task_id(task_id)
            trajectory = await generate_trajectory(
                messages=messages,
                task_id=task_id,
                state_mng=event_runner.state_manager
            )
            
            if not trajectory:
                return None
            
            # Apply filters using the overridable methods
            filtered_trajectory = []
            for item in trajectory:
                meta = item.get('meta', {})
                
                # Filter by agent (if agent_id exists)
                agent_id = meta.get('agent_id')
                if agent_id and not self.filter_by_agent(agent_id):
                    continue
                
                # Filter by step (if step exists)
                step = meta.get('step')
                if step is not None and not self.filter_by_step(step):
                    continue
                
                # Custom item filter
                if not self.filter_by_item(item):
                    continue
                
                filtered_trajectory.append(item)
            
            logger.info(
                f"Filtered trajectory for task {task_id}: "
                f"{len(filtered_trajectory)}/{len(trajectory)} items"
            )
            
            return filtered_trajectory if filtered_trajectory else None
            
        except Exception as e:
            logger.error(f"Failed to generate filtered trajectory for task {task_id}: {str(e)}")
            return None

class MemoryTrajectoryStrategy(TrajectoryStrategy):

    def filter_by_agent(self, agent_id: str) -> bool:
        """
        Override this method to filter by agent ID.

        Args:
            agent_id: The agent identifier

        Returns:
            bool: True to keep this item, False to filter it out
        """
        return True  # Default: keep all agents

    def filter_by_step(self, step: int) -> bool:
        """
        Override this method to filter by step number.

        Args:
            step: The step number

        Returns:
            bool: True to keep this item, False to filter it out
        """
        return True  # Default: keep all steps

    def filter_by_item(self, item: Dict[str, Any]) -> bool:
        """
        Override this method for custom filtering logic.

        Args:
            item: The trajectory item dictionary

        Returns:
            bool: True to keep this item, False to filter it out
        """
        return True  # Default: keep all items

    async def generate_trajectory_for_memory(self, swarm: 'Swarm', context: Context):
        if not swarm or not swarm.cur_agent:
            return {}
        from aworld.memory.main import MemoryFactory
        memory_items = MemoryFactory.instance().get_last_n(100, filters={
            "agent_id": swarm.cur_agent[0].id(),
            "session_id": context.session_id,
            "task_id": context.task_id,
            "include_summaried": True
        }, agent_memory_config=swarm.cur_agent[0].memory_config)

        # Convert memory items to OpenAI message format
        result = {}
        for i, item in enumerate(memory_items):
            # Check if item has to_dict method
            if hasattr(item, 'to_dict'):
                message = item.to_dict()
                # Add usage to the message if it exists in metadata
                if hasattr(item, 'metadata') and item.metadata and 'usage' in item.metadata:
                    message['usage'] = item.metadata['usage']
                result[i] = message
            else:
                # If item doesn't have to_dict, return the item as is
                result[i] = item

        return result

    async def generate(
            self,
            task_id: str,
            event_runner: 'TaskRunner'
    ) -> Optional[List[Dict[str, Any]]]:
        """Generate trajectory using the default strategy.

        Args:
            task_id (str): The unique identifier of the task
            event_runner (TaskRunner): The task runner instance

        Returns:
            Optional[List[Dict[str, Any]]]: Serialized trajectory data or None if failed
        """

        try:
            logger.info(f"Generating trajectory for task {task_id} using default strategy")

            swarm = event_runner.swarm
            context = event_runner.context
            trajectory = await self.generate_trajectory_for_memory(swarm=swarm, context=context)

            if trajectory and self.validate_trajectory(trajectory):
                logger.info(f"Successfully generated {len(trajectory)} trajectory items for task {task_id}")
                return trajectory
            else:
                logger.warning(f"Generated trajectory validation failed for task {task_id}")
                return None

        except Exception as e:
            logger.error(f"Failed to generate trajectory for task {task_id}: {str(e)}")
            return None

    def validate_trajectory(self, trajectory: List[Dict[str, Any]]) -> bool:
        return True
