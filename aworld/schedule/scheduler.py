# coding: utf-8
# Copyright (c) inclusionAI.
import asyncio
import time
from typing import Optional, List, Callable, Awaitable, Dict, Set

from aworld.logs.util import logger
from aworld.runners.hook.utils import run_hooks
from aworld.runners.task_manager import TaskManager
from aworld.schedule.strategy import ScheduleStrategy, create_strategy
from aworld.schedule.types import ScheduledTask, ResourceQuota, ScheduledTaskStatistics
from aworld.core.common import TaskStatusValue
from aworld.core.task import Task, TaskResponse


class TaskScheduler:
    """
    Task Scheduler for managing and executing scheduled tasks.

    The scheduler supports:
    - Multiple scheduling strategies (FIFO, Priority, DAG, Auto)
    - Resource quota management
    - Concurrent task execution
    - Periodic task scheduling
    - Task dependency resolution
    - Lifecycle hooks

    Examples:
        # Create scheduler with storage
        from aworld.core.storage.inmemory_store import InmemoryStorage
        storage = InmemoryStorage()
        manager = TaskManager(storage=storage)
        scheduler = TaskScheduler(task_manager=manager)

        # Add tasks
        task1 = ScheduledTask(id="task1", name="Task 1", priority=10)
        await scheduler.add_task(task1)

        # Set execution handler
        async def execute_handler(task):
            print(f"Executing {task.name}")
            await asyncio.sleep(1)
            return True

        scheduler.set_executor(execute_handler)

        # Run scheduler
        await scheduler.run(max_iterations=10)

        # Or run scheduler in background
        scheduler.start()
        # ... do other work ...
        await scheduler.stop()
    """

    def __init__(
        self,
        task_manager: TaskManager,
        strategy: Optional[ScheduleStrategy] = None,
        strategy_type: str = 'auto',
        resource_quota: Optional[ResourceQuota] = None,
        max_concurrent: int = 10,
        poll_interval: float = 1.0,
        enable_periodic: bool = True
    ):
        """
        Initialize TaskScheduler.

        Args:
            task_manager: TaskManager instance (required)
            strategy: Custom scheduling strategy (optional)
            strategy_type: Type of strategy if strategy is None ('auto', 'fifo', 'priority', 'dag')
            resource_quota: Resource quota for task execution
            max_concurrent: Maximum concurrent tasks
            poll_interval: Seconds between scheduling cycles
            enable_periodic: Enable periodic task scheduling
        """
        if task_manager is None:
            raise ValueError("task_manager is required")

        self.task_manager = task_manager
        self.strategy = strategy or create_strategy(strategy_type)
        self.resource_quota = resource_quota or ResourceQuota(max_concurrent=max_concurrent)

        self.poll_interval = poll_interval
        self.enable_periodic = enable_periodic

        # Execution state
        self._executor: Optional[Callable[[Task], Awaitable[bool]]] = None
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._stop_event = asyncio.Event()

        # Statistics
        self.statistics = ScheduledTaskStatistics()

        # Lifecycle hooks
        self._on_task_start: Optional[Callable[[Task], Awaitable[None]]] = None
        self._on_task_complete: Optional[Callable[[Task, bool], Awaitable[None]]] = None
        self._on_task_error: Optional[Callable[[Task, Exception], Awaitable[None]]] = None

    def set_executor(self, executor: Callable[[Task], Awaitable[bool]]):
        """
        Set the task executor function.

        Args:
            executor: Async function that takes a Task and returns bool (success)

        Examples:
            async def my_executor(task):
                print(f"Executing {task.name}")
                # ... do work ...
                return True

            scheduler.set_executor(my_executor)
        """
        self._executor = executor

    async def add_task(self, task: ScheduledTask, overwrite: bool = True) -> bool:
        """
        Add a task to the scheduler.

        Args:
            task: Task to add
            overwrite: Whether to overwrite existing task

        Returns:
            bool: True if successful
        """
        success = await self.task_manager.add_task(task, overwrite=overwrite)

        if success:
            logger.info(f"Added task to scheduler: {task.id} ({task.name})")

        return success

    async def add_tasks(self, tasks: List[ScheduledTask], overwrite: bool = True) -> int:
        """
        Add multiple tasks to the scheduler.

        Args:
            tasks: List of tasks to add
            overwrite: Whether to overwrite existing tasks

        Returns:
            int: Number of successfully added tasks
        """
        return await self.task_manager.add_batch(tasks, overwrite=overwrite)

    async def schedule(self, tasks: Optional[List[ScheduledTask]] = None) -> List[List[ScheduledTask]]:
        """Schedule tasks into execution batches using the configured strategy.

        Args:
            tasks: Tasks to schedule (default: get ready tasks from manager)

        Returns:
            List of task batches, where each batch can be executed in parallel
        """
        if tasks is None:
            tasks = await self.task_manager.get_ready()

        if not tasks:
            return []

        # Apply scheduling strategy
        batches = await self.strategy.schedule(tasks)
        logger.debug(f"Scheduled {len(tasks)} tasks into {len(batches)} batches")
        return batches

    async def execute(self, task: Task) -> TaskResponse:
        """Execute a single task.

        Args:
            task: Task to execute

        Returns:
            bool: True if successful
        """
        if self._executor is None:
            logger.error("No executor set. Use set_executor() to configure task execution.")
            return None

        # demo
        await run_hooks(task.context, "task_start", "scheduler", payload=task)
        try:
            # Mark task as started
            await self.task_manager.update_status(
                task.id,
                TaskStatusValue.RUNNING,
                started_at=time.time()
            )

            logger.info(f"Executing task: {task.id} ({task.name})")

            # Execute task
            success = await self._executor(task)

            # Mark task as completed
            await self.task_manager.update_status(
                task.id,
                TaskStatusValue.SUCCESS if success else TaskStatusValue.FAILED,
                completed_at=time.time()
            )

            # Update completed cache for dependency tracking
            if success:
                self.task_manager.mark_completed(task.id)

            logger.info(f"Task {'completed' if success else 'failed'}: {task.id}")

            # Handle periodic tasks
            if success and hasattr(task, 'is_periodic') and task.is_periodic:
                await self._reschedule_periodic_task(task)

            return None
        except Exception as e:
            logger.error(f"Error executing task {task.id}: {e}")

            # Mark task as failed
            await self.task_manager.update_status(
                task.id,
                TaskStatusValue.FAILED,
                completed_at=time.time()
            )

            return None

    async def _reschedule_periodic_task(self, task: ScheduledTask):
        """Reschedule a periodic task for next execution."""
        try:
            # Update next run time
            task.update_next_run_time()

            # Check if should continue
            if task.max_executions and task.execution_count >= task.max_executions:
                logger.info(f"Periodic task {task.id} reached max executions")
                return

            if task.end_time and time.time() > task.end_time:
                logger.info(f"Periodic task {task.id} reached end time")
                return

            # Reset for next execution
            task.task_status = "init"
            task.started_at = None
            task.completed_at = None

            # Update in storage
            await self.task_manager.update_task(task)

            logger.info(f"Rescheduled periodic task {task.id} for {task.next_run_time}")

        except Exception as e:
            logger.error(f"Failed to reschedule periodic task {task.id}: {e}")

    async def execute_batch(self, batch: List[Task]) -> List[bool]:
        """
        Execute a batch of tasks concurrently.

        Args:
            batch: Batch of tasks to execute

        Returns:
            List of success flags for each task
        """
        if not batch:
            return []

        # Check resource availability
        current_concurrent = len(self._active_tasks)
        if self.resource_quota.max_concurrent > 0:
            available_slots = self.resource_quota.max_concurrent - current_concurrent
            if available_slots <= 0:
                logger.warning("Max concurrent tasks reached, skipping batch")
                return [False] * len(batch)

            # Limit batch size to available slots
            batch = batch[:available_slots]

        # Execute tasks concurrently
        tasks = [self.execute(task) for task in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to False
        return [r if isinstance(r, bool) else False for r in results]

    async def run_once(self) -> int:
        """
        Run one scheduling cycle.

        Returns:
            int: Number of tasks executed
        """
        # Get ready tasks
        ready_tasks = await self.task_manager.get_ready()

        if not ready_tasks:
            return 0

        # Schedule tasks into batches
        batches = await self.schedule(ready_tasks)

        executed_count = 0

        # Execute batches
        for batch in batches:
            results = await self.execute_batch(batch)
            executed_count += sum(1 for r in results if r)

        return executed_count

    async def run(self, timeout: Optional[float] = None):
        """
        Run the scheduler for a limited time or iterations.

        Args:
            max_iterations: Maximum number of scheduling cycles (None = unlimited)
            timeout: Maximum time to run in seconds (None = unlimited)

        Examples:
            # Run for 10 iterations
            await scheduler.run(max_iterations=10)

            # Run for 60 seconds
            await scheduler.run(timeout=60)

            # Run until all tasks complete
            await scheduler.run()
        """
        start_time = time.time()
        logger.info("Starting scheduler")

        try:
            # Run one cycle
            executed = await self.run_once()

            # Check if no tasks to execute
            if executed == 0:
                pending = await self.task_manager.count(TaskStatusValue.INIT)
                if pending == 0:
                    logger.info("No more tasks to execute")
        except Exception as e:
            logger.error(f"Error in scheduler: {e}")
            raise
        finally:
            logger.info(f"Scheduler completed, time cost {time.time() - start_time}(s)")

    async def _scheduler_loop(self):
        """Main scheduler loop (for background execution)."""
        logger.info("Scheduler loop started")

        try:
            while self._running:
                # Check stop event with timeout
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.poll_interval
                    )
                    # Stop event was set
                    break
                except asyncio.TimeoutError:
                    # Timeout - continue with scheduling cycle
                    pass

                # Run one scheduling cycle
                await self.run_once()

        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}")
        finally:
            self._running = False
            logger.info("Scheduler loop stopped")

    def start(self):
        """
        Start the scheduler in background.

        Examples:
            scheduler.start()
            # ... scheduler runs in background ...
            await scheduler.stop()
        """
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._running = True
        self._stop_event.clear()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info("Scheduler started in background")

    async def stop(self, wait: bool = True):
        """
        Stop the background scheduler.

        Args:
            wait: Wait for scheduler to complete current cycle
        """
        if not self._running:
            logger.warning("Scheduler not running")
            return

        logger.info("Stopping scheduler...")

        self._running = False
        self._stop_event.set()

        if wait and self._scheduler_task:
            await self._scheduler_task

        # Cancel active tasks
        for task_id, task in self._active_tasks.items():
            if not task.done():
                task.cancel()
                logger.debug(f"Cancelled active task: {task_id}")

        self._active_tasks.clear()

        logger.info("Scheduler stopped")

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running

    async def get_statistics(self) -> dict:
        """
        Get scheduler statistics.

        Returns:
            Dictionary with scheduler statistics
        """
        stats = await self.task_manager.get_statistics()

        stats.update({
            "is_running": self.is_running,
            "active_tasks": len(self._active_tasks),
            "max_concurrent": self.resource_quota.max_concurrent,
            "strategy": self.strategy.__class__.__name__,
            "poll_interval": self.poll_interval,
        })

        return stats

    async def pause(self):
        """Pause the scheduler (can be resumed)."""
        if not self._running:
            logger.warning("Scheduler not running")
            return

        self._stop_event.set()
        logger.info("Scheduler paused")

    async def resume(self):
        """Resume the paused scheduler."""
        if self._running and self._stop_event.is_set():
            self._stop_event.clear()
            logger.info("Scheduler resumed")
        else:
            logger.warning("Scheduler not paused or not running")

    async def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a scheduled task.

        Args:
            task_id: ID of task to cancel

        Returns:
            bool: True if successful
        """
        # Cancel active execution
        if task_id in self._active_tasks:
            self._active_tasks[task_id].cancel()
            del self._active_tasks[task_id]

        # Update task status
        return await self.task_manager.update_status(
            task_id,
            TaskStatusValue.CANCELLED,
            completed_at=time.time()
        )

    async def cleanup(self, before_time: Optional[float] = None):
        """
        Clean up old completed tasks.

        Args:
            before_time: Remove tasks completed before this time
        """
        removed = await self.task_manager.cleanup_completed(before_time=before_time)
        logger.info(f"Cleaned up {removed} old tasks")
        return removed