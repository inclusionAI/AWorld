# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Task State Service - Manages task state and working state operations.

This service abstracts task state management operations from ApplicationContext,
providing a clean interface for managing task input, output, status, and working state.
"""
import abc
from typing import Optional, List, Dict, Any

from aworld.core.context.amni.state.task_state import ApplicationTaskContextState, ApplicationAgentState, TaskWorkingState, SubTask
from aworld.core.context.amni.state.common import TaskInput, TaskOutput, WorkingState
from aworld.core.task import TaskStatus
from aworld.memory.models import MemoryMessage


class ITaskStateService(abc.ABC):
    """Interface for task state management operations."""
    
    @abc.abstractmethod
    def get_task_input(self) -> TaskInput:
        """Get current task input."""
        pass
    
    @abc.abstractmethod
    def set_task_input(self, task_input: TaskInput) -> None:
        """Set task input."""
        pass
    
    @abc.abstractmethod
    def get_task_output(self) -> TaskOutput:
        """Get current task output."""
        pass
    
    @abc.abstractmethod
    def set_task_output(self, task_output: TaskOutput) -> None:
        """Set task output."""
        pass
    
    @abc.abstractmethod
    def get_task_status(self) -> TaskStatus:
        """Get current task status."""
        pass
    
    @abc.abstractmethod
    def set_task_status(self, status: TaskStatus) -> None:
        """Set task status."""
        pass
    
    @abc.abstractmethod
    def get_working_state(self, namespace: str = "default") -> Optional[WorkingState]:
        """Get working state for the given namespace."""
        pass
    
    @abc.abstractmethod
    def get_kv_store(self, namespace: str = "default") -> Dict[str, Any]:
        """Get key-value store for the namespace."""
        pass
    
    @abc.abstractmethod
    def put_kv(self, key: str, value: Any, namespace: str = "default") -> None:
        """Put a value into key-value store."""
        pass
    
    @abc.abstractmethod
    def get_kv(self, key: str, namespace: str = "default") -> Any:
        """Get a value from key-value store."""
        pass
    
    @abc.abstractmethod
    def get_sub_task_list(self) -> Optional[List[SubTask]]:
        """Get list of sub tasks."""
        pass
    
    @abc.abstractmethod
    def add_sub_task(self, sub_task_input: TaskInput, task_type: str = 'normal') -> None:
        """Add a sub task to the task list."""
        pass
    
    @abc.abstractmethod
    def set_agent_state(self, agent_id: str, agent_state: ApplicationAgentState) -> None:
        """Set agent state for the given agent_id."""
        pass
    
    @abc.abstractmethod
    def get_agent_state(self, agent_id: str) -> Optional[ApplicationAgentState]:
        """Get agent state for the given agent_id."""
        pass
    
    @abc.abstractmethod
    def has_agent_state(self, agent_id: str) -> bool:
        """Check if agent state exists for the given agent_id."""
        pass


class TaskStateService(ITaskStateService):
    """
    Task State Service implementation.
    
    Manages task state including task input/output, status, working state, and agent states.
    Provides operations for managing sub tasks and key-value storage.
    """
    
    def __init__(self, context):
        """
        Initialize TaskStateService with ApplicationContext.
        
        Args:
            context: ApplicationContext instance that provides access to task_state
        """
        self._context = context
    
    @property
    def _task_state(self) -> ApplicationTaskContextState:
        """Get the underlying task state."""
        return self._context.task_state
    
    def get_task_input(self) -> TaskInput:
        """Get current task input."""
        return self._task_state.task_input
    
    def set_task_input(self, task_input: TaskInput) -> None:
        """Set task input."""
        self._task_state.set_task_input(task_input)
    
    def get_task_output(self) -> TaskOutput:
        """Get current task output."""
        return self._task_state.task_output
    
    def set_task_output(self, task_output: TaskOutput) -> None:
        """Set task output."""
        self._task_state.set_task_output(task_output)
    
    def get_task_status(self) -> TaskStatus:
        """Get current task status."""
        return self._task_state.working_state.status
    
    def set_task_status(self, status: TaskStatus) -> None:
        """Set task status."""
        self._task_state.working_state.status = status
    
    def get_working_state(self, namespace: str = "default") -> Optional[WorkingState]:
        """Get working state for the given namespace."""
        if namespace == "default":
            return self._task_state.working_state
        agent_state = self.get_agent_state(namespace)
        if agent_state:
            return agent_state.working_state
        return None
    
    def get_kv_store(self, namespace: str = "default") -> Dict[str, Any]:
        """Get key-value store for the namespace."""
        working_state = self.get_working_state(namespace)
        if working_state:
            return working_state.kv_store
        return {}
    
    def put_kv(self, key: str, value: Any, namespace: str = "default") -> None:
        """Put a value into key-value store."""
        working_state = self.get_working_state(namespace)
        if working_state:
            working_state.kv_store[key] = value
    
    def get_kv(self, key: str, namespace: str = "default") -> Any:
        """Get a value from key-value store."""
        working_state = self.get_working_state(namespace)
        if working_state:
            return working_state.kv_store.get(key)
        return None
    
    def get_sub_task_list(self) -> Optional[List[SubTask]]:
        """Get list of sub tasks."""
        return self._task_state.working_state.sub_task_list
    
    def add_sub_task(self, sub_task_input: TaskInput, task_type: str = 'normal') -> None:
        """Add a sub task to the task list."""
        from aworld.core.context.amni.state.task_state import SubTask
        
        sub_task = SubTask(
            task_id=sub_task_input.task_id,
            input=sub_task_input,
            status='init',
            task_type=task_type
        )
        self._task_state.working_state.sub_task_list.append(sub_task)
    
    def upsert_sub_task(self, sub_task_input: TaskInput, task_type: str = 'normal') -> None:
        """Update or insert a sub task."""
        self._task_state.working_state.upsert_subtask_by_input(sub_task_input, task_type=task_type)
    
    def set_agent_state(self, agent_id: str, agent_state: ApplicationAgentState) -> None:
        """Set agent state for the given agent_id."""
        self._task_state.set_agent_state(agent_id, agent_state)
    
    def get_agent_state(self, agent_id: str) -> Optional[ApplicationAgentState]:
        """Get agent state for the given agent_id."""
        return self._task_state.get_agent_state(agent_id)
    
    def has_agent_state(self, agent_id: str) -> bool:
        """Check if agent state exists for the given agent_id."""
        return self._task_state.has_agent_state(agent_id)
    
    def get_history_messages(self, namespace: str = "default") -> List[MemoryMessage]:
        """Get history messages for the namespace."""
        working_state = self.get_working_state(namespace)
        if working_state:
            return working_state.history_messages or []
        return []
    
    def get_user_profiles(self, namespace: str = "default") -> List:
        """Get user profiles for the namespace."""
        working_state = self.get_working_state(namespace)
        if working_state:
            return working_state.user_profiles or []
        return []
    
    def get_model_config(self):
        """Get model configuration from task state."""
        return self._task_state.model_config

