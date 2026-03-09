# coding: utf-8
# Copyright (c) inclusionAI.
import asyncio
import time
from typing import Optional, List, Callable, Awaitable, Dict, Set, Any

from aworld.logs.util import logger
from aworld.runners.hook.hooks import Hook
from aworld.runners.hook.utils import run_hooks
from aworld.runners.runtime_engine import RuntimeEngine
from aworld.runners.task_manager import TaskManager
from aworld.runners.utils import runtime_engine
from aworld.schedule.strategy import ScheduleStrategy, create_strategy
from aworld.schedule.types import ScheduledTask, ResourceQuota, TaskStatistics
from aworld.core.common import TaskStatus
from aworld.core.task import Task, TaskResponse
from aworld.utils.run_util import exec_tasks


class TaskScheduler:
    """
    Task Scheduler for managing and executing scheduled tasks.

    The scheduler supports:
    - Multiple scheduling strategies (FIFO, Priority, DAG, Auto, ...)
    - Resource quota management
    - Concurrent task execution
    - Periodic task scheduling
    - Task dependency resolution
    - Lifecycle hooks

    Examples:
        # Create scheduler with storage
        from aworld.core.storage.inmemory_store import InmemoryStorage
        manager = TaskManager(storage=InmemoryStorage())
        scheduler = TaskScheduler(task_manager=manager)

        # Add tasks
        task1 = ScheduledTask(id="task1", name="Task 1", priority=10)
        await scheduler.add_task(task1)

        # Set execution handler
        async def execute_handler(task):
            print(f"Executing {task.name}")
            await asyncio.sleep(1)
            return True

        # Run scheduler
        await scheduler.run()

        # Or run scheduler in background
        scheduler.start()
        # ... do other work ...
        await scheduler.stop()
    """

    def __init__(
            self,
            task_manager: TaskManager = TaskManager(),
            strategy: Optional[ScheduleStrategy] = None,
            strategy_type: str = 'auto',
            executor: Optional[Callable[[Task], Awaitable[TaskResponse]]] = None,
            resource_quota: Optional[ResourceQuota] = None,
            max_concurrent: int = 10,
            poll_interval: float = 1.0,
            hooks: Dict[str, Hook] = None
    ):
        """
        Initialize TaskScheduler.

        Args:
            task_manager: TaskManager instance (required)
            strategy: Custom scheduling strategy (optional)
            strategy_type: Type of schedule strategy ('auto', 'fifo', 'priority', 'dag', 'custom_name')
            executor: Async function to execute a task (optional, default uses RuntimeEngine)
            resource_quota: Resource quota for task execution
            max_concurrent: Maximum concurrent tasks
            poll_interval: Seconds between scheduling cycles
        """
        self.task_manager = task_manager
        self.strategy = strategy or create_strategy(strategy_type)
        self.resource_quota = resource_quota or ResourceQuota(max_concurrent=max_concurrent)
        self.poll_interval = poll_interval

        # Execution state
        self._executor: Optional[Callable[[Task], Awaitable[TaskResponse]]] = executor or execute_schedulable_tasks
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._stop_event = asyncio.Event()

        # Statistics
        self.statistics = TaskStatistics()

    @property
    def executor(self):
        return self._executor

    @executor.setter
    def executor(self, executor: Callable[[Task], Awaitable[TaskResponse]]):
        """
        Set the task executor function.

        Args:
            executor: Async function that takes a Task and returns bool (success)

        Examples:
            async def my_executor(task):
                print(f"Executing {task.name}")
                # ... do work ...
                return TaskResponse(success=True, ...)

            scheduler.executor = my_executor
        """
        self._executor = executor

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
        if not self._executor:
            res = await execute_schedulable_tasks(await runtime_engine(), [task])
            return res.get(task.id)

        try:
            # Mark task as started
            await self.task_manager.update_status(
                task.id,
                TaskStatus.RUNNING,
                started_at=time.time()
            )

            logger.info(f"Executing task: {task.id} ({task.name})")

            # Execute task
            result: TaskResponse = await self._executor(task)

            # Mark task as completed
            await self.task_manager.update_status(
                task.id,
                TaskStatus.SUCCESS if result.success else TaskStatus.FAILED,
                completed_at=time.time()
            )

            # Update completed cache for dependency tracking
            if result.success:
                self.task_manager.mark_completed(task.id)

            logger.info(f"Task {'completed' if result.success else 'failed'}: {task.id}")

            # Handle periodic tasks
            if result.success and hasattr(task, 'is_periodic') and task.is_periodic:
                await self._reschedule_periodic_task(task)

            return result
        except Exception as e:
            logger.error(f"Error executing task {task.id}: {e}")

            # Mark task as failed
            await self.task_manager.update_status(
                task.id,
                TaskStatus.FAILED,
                completed_at=time.time()
            )
            return TaskResponse()

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
            task.task_status = TaskStatus.INIT
            task.started_at = None
            task.completed_at = None

            # Update in storage
            await self.task_manager.update_task(task)

            logger.info(f"Rescheduled periodic task {task.id} for {task.next_run_time}")

        except Exception as e:
            logger.error(f"Failed to reschedule periodic task {task.id}: {e}")

    async def execute_batch(self, batch: List[Task]) -> Dict[str, TaskResponse]:
        """Execute a batch of tasks concurrently.

        Args:
            batch: Batch of tasks to execute

        Returns:
            Dict of response for each task
        """
        if not batch:
            return {}

        # Check resource availability
        current_concurrent = len(self._active_tasks)
        if self.resource_quota.max_concurrent > 0:
            available_slots = self.resource_quota.max_concurrent - current_concurrent
            if available_slots <= 0:
                logger.warning("Max concurrent tasks reached, skipping batch")
                return {}

            # Limit batch size to available slots
            batch = batch[:available_slots]

        # Execute tasks concurrently
        tasks = [self.execute(task) for task in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        return {res.id: res for res in results}

    async def _run_once(self) -> int:
        """Run one scheduling cycle.

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
        """Run the scheduler for a limited time or iterations.

        Args:
            timeout: Maximum time to run in seconds (None = unlimited)

        Examples:
            # Run for 60 seconds
            await scheduler.run(timeout=60)

            # Run until all tasks complete
            await scheduler.run()
        """
        start_time = time.time()
        logger.info("Starting scheduler")

        try:
            # Run one cycle
            executed = await self._run_once()

            # Check if no tasks to execute
            if executed == 0:
                pending = await self.task_manager.count(TaskStatus.INIT)
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
                await self._run_once()
        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}")
        finally:
            self._running = False
            logger.info("Scheduler loop stopped")

    def start(self):
        """Start the scheduler in background.

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
        """Stop the background scheduler.

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

        logger.info("Scheduler stopped!")

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running

    async def get_statistics(self) -> dict:
        """Get scheduler statistics.

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

    async def add_task(self, task: ScheduledTask, overwrite: bool = True) -> bool:
        """Add a task to the scheduler.

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
        """Add multiple tasks to the scheduler."""
        return await self.task_manager.add_batch(tasks, overwrite=overwrite)

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a scheduled task.

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
            TaskStatus.CANCELLED,
            completed_at=time.time()
        )

    async def cleanup(self, before_time: Optional[float] = None):
        """Clean up old completed tasks.

        Args:
            before_time: Remove tasks completed before this time
        """
        removed = await self.task_manager.cleanup_completed(before_time=before_time)
        logger.info(f"Cleaned up {removed} old tasks")
        return removed


async def execute_schedulable_tasks(runtime_engine: RuntimeEngine, tasks: List[Task]) -> Dict[str, Any]:
    """Execute a list of scheduled tasks using the runtime engine, updates task status and start time before execution."""
    if not tasks:
        return {}

    funcs = []
    # Wrap each task in an async executor function
    for scheduled_task in tasks:
        async def task_execute(st=scheduled_task):
            st.task_status = TaskStatus.RUNNING
            st.started_at = time.time()
            res = await exec_tasks(tasks=[st])
            task_response = res.get(st.id)
            if task_response:
                st.task_status = task_response.status
            st.completed_at = time.time()
            return res

        funcs.append(task_execute)

    results = await runtime_engine.execute(funcs)
    logger.info(f"{runtime_engine.name} execute {len(tasks)} tasks finished")
    return results
