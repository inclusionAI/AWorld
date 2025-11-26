# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Trajectory generation strategy interface and default implementation.

This module provides an extensible strategy pattern for generating training trajectories
from task execution. Users can implement custom strategies by extending TrajectoryStrategy.
"""

import abc
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from aworld.core.agent.swarm import Swarm
from aworld.core.context.base import Context
from aworld.logs.util import logger
from aworld.memory.main import MemoryFactory

if TYPE_CHECKING:
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
            Optional[List[Dict[str, Any]]]: List of trajectory data rows in dictionary format,
                or None if generation fails. Each dict should contain:
                - exp_meta: Metadata about the experience (task_id, agent_id, step, etc.)
                - exp_data: Experience data (state, actions, messages, etc.)
                - id: Unique identifier for this data row
                
        Raises:
            Exception: Implementation-specific exceptions during trajectory generation
        """
        pass
    
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
    """Default trajectory generation strategy.
    
    This strategy implements the original trajectory generation logic:
    1. Retrieve all messages for the task
    2. Filter agent-related messages
    3. Convert messages to DataRow format using TrajectoryDataset
    4. Return serialized trajectory data
    
    This is used as the fallback when no custom strategy is provided.
    """
    
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
        from aworld.dataset.trajectory_dataset import generate_trajectory
        
        try:
            logger.info(f"Generating trajectory for task {task_id} using default strategy")
            
            # Get all messages for this task
            messages = await event_runner.event_mng.messages_by_task_id(task_id)
            
            # Generate trajectory using the existing implementation
            trajectory = await generate_trajectory(
                messages=messages,
                task_id=task_id,
                state_mng=event_runner.state_manager
            )
            
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
        if not trajectory:
            return False
        # Basic validation: check required fields
        for item in trajectory:
            if not isinstance(item, dict):
                logger.warning(f"Invalid trajectory item type: {type(item)}")
                return False
            if 'id' not in item or 'exp_meta' not in item or 'exp_data' not in item:
                logger.warning(f"Missing required fields in trajectory item: {item.keys()}")
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
                exp_meta = item.get('exp_meta', {})
                
                # Filter by agent (if agent_id exists)
                agent_id = exp_meta.get('agent_id')
                if agent_id and not self.filter_by_agent(agent_id):
                    continue
                
                # Filter by step (if step exists)
                step = exp_meta.get('step')
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

    async def generate_trajectory_for_memory(self, swarm: Swarm, context: Context):
        if not swarm or not swarm.cur_agent:
            return {}
        memory_items = MemoryFactory.instance().get_last_n(100, filters={
            "agent_id": swarm.cur_agent[0].id(),
            "session_id": context.session_id,
            "task_id": context.task_id,
            "include_summaried": True
        }, agent_memory_config=swarm.cur_agent[0].memory_config)

        # Convert memory items to OpenAI message format
        result = []
        for i, item in enumerate(memory_items):
            # Check if item has to_openai_message method
            if hasattr(item, 'to_openai_message'):
                message = item.to_openai_message()
                # Add usage to the message if it exists in metadata
                if hasattr(item, 'metadata') and item.metadata and 'usage' in item.metadata:
                    message['usage'] = item.metadata['usage']
                result.append(message)
            else:
                # If item doesn't have to_openai_message, return the item as is
                result.append(item)

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


