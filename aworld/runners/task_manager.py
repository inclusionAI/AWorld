# coding: utf-8
# Copyright (c) inclusionAI.
import time
from typing import List, Optional, Set

from aworld.core.storage.base import Storage
from aworld.core.storage.data import Data
from aworld.core.storage.inmemory_store import InmemoryStorage
from aworld.core.task import Task
from aworld.logs.util import logger

from aworld.core.common import TaskStatus


class TaskManager:
    """High-level task store providing simple API for task persistence.

    TaskStore acts as a facade over TaskStorage, providing:
    - Simple CRUD operations
    - Task querying and filtering
    - Status-based retrieval
    - Ready task detection

    Examples:
        # Initialize with storage (required)

        manager = TaskManager(storage=InMemoryStorage())

        # Add task
        task = ScheduledTask(id="task1_id", name="My Task")
        await manager.add_task(task)

        # Get task
        task = await manager.get_task("task1_id")

        # List tasks
        all_tasks = await manager.list()
        pending_tasks = await manager.list(status=TaskStatusValue.INIT)

        # Get ready tasks
        ready = await manager.get_ready()
    """

    def __init__(self, storage: Storage = InmemoryStorage()):
        if storage is None:
            raise ValueError("storage parameter is required")

        if not isinstance(storage, Storage):
            raise TypeError(f"storage must be TaskStorage instance, got {type(storage)}")

        self.storage = storage
        self._cache_completed: Set[str] = set()

    async def add_task(self, task: Task, overwrite: bool = True) -> bool:
        """Add a task to the store.

        Args:
            task: Task to add
            overwrite: Whether to overwrite if task exists

        Returns:
            bool: True if successful

        Raises:
            ValueError: If task.id is empty
        """
        if not task.id:
            raise ValueError("Task ID cannot be empty")

        try:
            success = await self.storage.create_data(
                Data(value=task),
                block_id=task.id,
                overwrite=overwrite
            )

            if success:
                logger.debug(f"Added task: {task.id}")

            return success
        except Exception as e:
            logger.error(f"Failed to add task {task.id}: {e}")
            return False

    async def add_batch(self, tasks: List[Task], overwrite: bool = True) -> int:
        """Add multiple tasks in batch.

        Args:
            tasks: List of tasks to add
            overwrite: Whether to overwrite existing tasks

        Returns:
            int: Number of successfully added tasks
        """
        success_count = 0
        for task in tasks:
            if await self.add_task(task, overwrite=overwrite):
                success_count += 1

        logger.debug(f"Added {success_count}/{len(tasks)} tasks")
        return success_count

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID.

        Args:
            task_id: Task ID

        Returns:
            Task or None if not found
        """
        try:
            tasks = await self.storage.get_data_items(block_id=task_id)
            return tasks[0].value if tasks else None
        except Exception as e:
            logger.error(f"Failed to get task {task_id}: {e}")
            return None

    async def update_task(self, task: Task) -> bool:
        """Update an existing task.

        Args:
            task: Updated task

        Returns:
            bool: True if successful
        """
        try:
            success = await self.storage.update_data(
                Data(value=task),
                block_id=task.id,
                exists=True
            )

            if success:
                logger.debug(f"Updated task: {task.id}")
            return success
        except Exception as e:
            logger.error(f"Failed to update task {task.id}: {e}")
            return False

    async def update_status(
            self,
            task_id: str,
            status: str,
            **fields
    ) -> bool:
        """Update task status and optional fields.

        Args:
            task_id: Task ID
            status: New status
            **fields: Additional fields to update (e.g., started_at, completed_at)

        Returns:
            bool: True if successful
        """
        task = await self.get_task(task_id)
        if not task:
            logger.warning(f"Task {task_id} not found")
            return False

        task.task_status = status

        for field, value in fields.items():
            if hasattr(task, field):
                setattr(task, field, value)

        return await self.update_task(task)

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task.

        Args:
            task_id: Task ID

        Returns:
            bool: True if successful
        """
        try:
            success = await self.storage.delete_data(
                task_id,
                block_id=task_id,
                exists=False
            )

            if success:
                logger.debug(f"Deleted task: {task_id}")
                self._cache_completed.discard(task_id)

            return success
        except Exception as e:
            logger.error(f"Failed to delete task {task_id}: {e}")
            return False

    async def exists(self, task_id: str) -> bool:
        """Check if a task exists.

        Args:
            task_id: Task ID

        Returns:
            bool: True if task exists
        """
        return await self.get_task(task_id) is not None

    async def list(
            self,
            status: Optional[str] = None,
            limit: Optional[int] = None,
            offset: int = 0
    ) -> List[Task]:
        """List tasks with optional filtering.

        Args:
            status: Filter by status (optional)
            limit: Maximum number of tasks to return
            offset: Number of tasks to skip

        Returns:
            List of tasks
        """
        try:
            if status:
                tasks = await self.get_tasks_by_status(status)
            else:
                tasks = await self.storage.get_data_items()

            # Apply offset and limit
            tasks = tasks[offset:] if offset > 0 else tasks
            tasks = tasks[:limit] if limit else tasks
            return tasks
        except Exception as e:
            logger.error(f"Failed to list tasks: {e}")
            return []

    async def get_ready(
            self,
            current_time: Optional[float] = None,
            limit: Optional[int] = None
    ) -> List[Task]:
        """Get tasks that are ready to execute.

        Args:
            current_time: Current timestamp (default: now)
            limit: Maximum number of tasks

        Returns:
            List of ready tasks sorted by priority and time
        """
        current_time = current_time if current_time else time.time()

        try:
            ready_tasks = await self.get_ready_tasks(current_time, limit)
            return ready_tasks
        except Exception as e:
            logger.error(f"Failed to get ready tasks: {e}")
            return []

    async def get_pending(self, limit: Optional[int] = None) -> List[Task]:
        """Get all pending (INIT status) tasks."""
        return await self.list(status=TaskStatus.INIT, limit=limit)

    async def get_running(self, limit: Optional[int] = None) -> List[Task]:
        """Get all running tasks."""
        return await self.list(status=TaskStatus.RUNNING, limit=limit)

    async def get_completed(self, limit: Optional[int] = None) -> List[Task]:
        """Get all completed (SUCCESS status) tasks."""
        return await self.list(status=TaskStatus.SUCCESS, limit=limit)

    async def get_failed(self, limit: Optional[int] = None) -> List[Task]:
        """Get all failed tasks."""
        return await self.list(status=TaskStatus.FAILED, limit=limit)

    async def get_periodic(self) -> List[Task]:
        """Get all periodic tasks (with cron expression)."""
        try:
            return await self.get_periodic_tasks()
        except Exception as e:
            logger.error(f"Failed to get periodic tasks: {e}")
            return []

    async def count(self, status: Optional[str] = None) -> int:
        """
        Count tasks, optionally filtered by status.

        Args:
            status: Filter by status (optional)

        Returns:
            Number of tasks
        """
        try:
            if status:
                return await self.count_by_status(status)
            else:
                return await self.storage.size()
        except Exception as e:
            logger.error(f"Failed to count tasks: {e}")
            return 0

    async def clear(self) -> bool:
        """
        Clear all tasks (use with caution).

        Returns:
            bool: True if successful
        """
        try:
            await self.storage.delete_all()
            self._cache_completed.clear()
            logger.warning("Cleared all tasks from store")
            return True
        except Exception as e:
            logger.error(f"Failed to clear tasks: {e}")
            return False

    async def cleanup_completed(
            self,
            before_time: Optional[float] = None,
            keep_periodic: bool = True
    ) -> int:
        """Clean up old completed tasks.

        Args:
            before_time: Remove tasks completed before this time (default: 7 days ago)
            keep_periodic: Keep periodic tasks even if completed

        Returns:
            int: Number of tasks removed
        """
        if before_time is None:
            before_time = time.time() - (7 * 24 * 3600)

        try:
            completed_tasks = await self.get_completed()
            removed_count = 0

            for task in completed_tasks:
                # Check if task has completed_at attribute (for compatibility)
                completed_at = getattr(task, 'completed_at', None)
                is_periodic = getattr(task, 'is_periodic', False)

                # Determine if should remove
                should_remove = False
                if completed_at is not None and completed_at < before_time:
                    # Only remove if not keeping periodic tasks or task is not periodic
                    if not keep_periodic or not is_periodic:
                        should_remove = True
                elif completed_at is None:
                    # For tasks without completed_at, use created_at or current time
                    created_at = getattr(task, 'created_at', None)
                    if created_at is not None and created_at < before_time:
                        if not keep_periodic or not is_periodic:
                            should_remove = True

                if should_remove:
                    if await self.delete_task(task.id):
                        removed_count += 1

            logger.info(f"Cleaned up {removed_count} completed tasks")
            return removed_count
        except Exception as e:
            logger.error(f"Failed to cleanup completed tasks: {e}")
            return 0

    async def get_statistics(self) -> dict:
        """
        Get task statistics.

        Returns:
            Dictionary with task counts by status
        """
        try:
            total = await self.count()
            pending = await self.count(TaskStatus.INIT)
            running = await self.count(TaskStatus.RUNNING)
            completed = await self.count(TaskStatus.SUCCESS)
            failed = await self.count(TaskStatus.FAILED)

            return {
                "total": total,
                "pending": pending,
                "running": running,
                "completed": completed,
                "failed": failed,
            }
        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            return {}

    def mark_completed(self, task_id: str):
        """Mark a task as completed in cache (for dependency tracking)."""
        self._cache_completed.add(task_id)

    def get_completed_ids(self) -> Set[str]:
        """Get set of completed task IDs from cache."""
        return self._cache_completed.copy()

    async def get_tasks_by_status(self, status: str, limit: Optional[int] = None) -> List[Task]:
        # Create condition for status filtering
        from aworld.core.storage.condition import Condition

        try:
            condition = Condition().add("task_status", "==", status)
            tasks = await self.storage.select_data(condition)

            # Sort by next_run_time (earlier first), with None values at the end
            if tasks and hasattr(tasks[0], "next_run_time"):
                tasks.sort(key=lambda t: (t.next_run_time is None, t.next_run_time or 0))
            if limit:
                tasks = tasks[:limit]
            return tasks
        except Exception as e:
            logger.error(f"Failed to get tasks by status {status}: {e}")
            return []

    async def get_ready_tasks(self,
                              current_time: Optional[float] = None,
                              limit: Optional[int] = None) -> List[Task]:
        """Get tasks that are ready to execute.

        A task is ready if:
        1. Status is INIT (pending)
        2. Current time >= next_run_time (or scheduled_time)
        3. All dependencies are completed
        4. Within start_time and end_time constraints
        5. Not exceeded max_executions

        Args:
            current_time: Current timestamp (default: now)
            limit: Maximum number of tasks to return

        Returns:
            List of ready tasks sorted by priority (high to low) and next_run_time
        """
        current_time = current_time if current_time else time.time()
        try:
            pending_tasks = await self.get_tasks_by_status(TaskStatus.INIT)
            ready_tasks = []
            for task in pending_tasks:
                # Check if task has is_ready method (for ScheduledTask compatibility)
                if hasattr(task, 'is_ready') and callable(getattr(task, 'is_ready')):
                    # Use task's is_ready method
                    if task.is_ready(self._cache_completed, current_time):
                        ready_tasks.append(task)
                else:
                    # For basic Task without is_ready, check basic readiness
                    # Check dependencies if task has them
                    dependencies = getattr(task, 'dependencies', [])
                    deps_ready = all(dep_id in self._cache_completed for dep_id in dependencies)

                    # Check time constraints if task has next_run_time
                    next_run_time = getattr(task, 'next_run_time', None)
                    time_ready = True
                    if next_run_time is not None:
                        time_ready = current_time >= next_run_time

                    if deps_ready and time_ready:
                        ready_tasks.append(task)

            # Sort by priority (higher first) and next_run_time (earlier first)
            if ready_tasks:
                def sort_key(t):
                    priority = getattr(t, "priority", 0)
                    next_run_time = getattr(t, "next_run_time", None)
                    return (-priority, next_run_time if next_run_time is not None else float('inf'))

                ready_tasks.sort(key=sort_key)

            ready_tasks = ready_tasks[:limit] if limit else ready_tasks
            return ready_tasks
        except Exception as e:
            logger.error(f"Failed to get ready tasks: {e}")
            return []

    async def get_periodic_tasks(self) -> List[Task]:
        """Get all periodic tasks (tasks with cron expression).

        Returns:
            List of periodic tasks
        """
        try:
            # Get all tasks
            all_tasks = await self.storage.get_data_items()

            # Filter periodic tasks
            return [task for task in all_tasks if hasattr(task, 'is_periodic') and task.is_periodic]
        except Exception as e:
            logger.error(f"Failed to get periodic tasks: {e}")
            return []

    async def count_by_status(self, status: str) -> int:
        """Count tasks by status.

        Args:
            status: Task status to count

        Returns:
            Number of tasks with the specified status
        """
        try:
            from aworld.core.storage.condition import Condition

            # Create condition for status filtering
            condition = Condition().add("task_status", "==", status)
            count = await self.storage.size(condition)

            return count
        except Exception as e:
            logger.error(f"Failed to count tasks by status {status}: {e}")
            return 0
