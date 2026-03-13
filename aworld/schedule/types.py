# coding: utf-8
# Copyright (c) inclusionAI.
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Set

from aworld.core.common import TaskStatus
from aworld.core.task import Task


@dataclass
class SchedulableTask(Task):
    """Instant schedulable task with dependencies and priority."""
    priority: int = 0
    retry_count: int = 0
    dependencies: List[str] = field(default_factory=list)

    # estimated resources
    estimated_cpu: float = 0.0
    estimated_memory: float = 0.0
    estimated_time: float = 0.0

    created_at: float = field(default_factory=time.time)
    scheduled_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __lt__(self, other):
        if not isinstance(other, SchedulableTask):
            return NotImplemented
        return self.priority < other.priority

    @property
    def status(self) -> TaskStatus:
        return self.task_status

    def is_ready(self, completed_tasks: Set[str]) -> bool:
        """Check if task is ready to execute (all dependencies completed)."""
        return all(dep_id in completed_tasks for dep_id in self.dependencies)

    def can_retry(self) -> bool:
        """Check if task can be retried."""
        return self.retry_count < self.max_retry_count

    def duration(self) -> float:
        """Get task execution duration."""
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return 0.0


@dataclass
class ScheduledTask(SchedulableTask):
    """Unified scheduled task supporting cron-like scheduling, one-time execution, and delayed execution.

    Scheduling modes (priority order):
    1. Cron expression: Use cron_expression for periodic tasks (e.g., "0 0 * * *" for daily at midnight)
    2. One-time scheduled: Use scheduled_time for one-time execution at specific time
    3. Delayed execution: Use delay for execution after specified seconds
    4. Instant: If none of above is set, execute immediately

    Examples:
        # Cron periodic task (every 5 minutes)
        ScheduledTask(..., cron_expression="*/5 * * * *")

        # One-time scheduled task (at specific timestamp)
        ScheduledTask(..., scheduled_time=1678886400.0)

        # Delayed task (execute after 60 seconds)
        ScheduledTask(..., delay=60.0)

        # Instant task (execute immediately)
        ScheduledTask(...)
    """

    # Cron expression for periodic tasks (e.g., "*/5 * * * *" for every 5 minutes)
    # Format: minute hour day month weekday
    cron_expression: Optional[str] = None

    # Scheduled time for one-time execution (Unix timestamp)
    scheduled_time: Optional[float] = None

    # Delay in seconds for delayed execution
    delay: Optional[float] = None

    # Time range constraints
    start_time: Optional[float] = None  # Start time (Unix timestamp)
    end_time: Optional[float] = None    # End time (Unix timestamp)

    # Execution limits
    max_executions: Optional[int] = None  # Max number of executions (for cron tasks)
    execution_count: int = 0              # Current execution count

    # Next execution time (calculated from cron/scheduled_time/delay)
    next_run_time: Optional[float] = None

    def __post_init__(self):
        """Initialize next_run_time based on scheduling mode."""
        if self.next_run_time is None:
            if self.cron_expression:
                # Calculate next run time from cron expression
                self.next_run_time = self._calculate_next_cron_time()
            elif self.scheduled_time is not None:
                # One-time scheduled task
                self.next_run_time = self.scheduled_time
            elif self.delay is not None:
                # Delayed task
                self.next_run_time = self.created_at + self.delay
            else:
                # Instant task (no scheduling)
                self.next_run_time = self.created_at

        if self.start_time is None:
            self.start_time = self.created_at

    def __lt__(self, other):
        if not isinstance(other, ScheduledTask):
            return NotImplemented
        # Compare by next_run_time, then by priority
        if self.next_run_time != other.next_run_time:
            return (self.next_run_time or 0) < (other.next_run_time or 0)
        return self.priority < other.priority

    @property
    def is_periodic(self) -> bool:
        """Check if this is a periodic task."""
        return self.cron_expression is not None

    @property
    def is_one_time(self) -> bool:
        """Check if this is a one-time task."""
        return self.cron_expression is None

    def is_ready(self, completed_tasks: Set[str], current_time: Optional[float] = None) -> bool:
        """Check if task is ready to execute."""
        if current_time is None:
            current_time = time.time()

        # Check time range
        if self.start_time is not None and current_time < self.start_time:
            return False
        if self.end_time is not None and current_time > self.end_time:
            return False

        # Check max executions
        if self.max_executions is not None and self.execution_count >= self.max_executions:
            return False

        # Check if time has come
        time_ready = self.next_run_time is not None and current_time >= self.next_run_time

        # Check dependencies
        deps_ready = all(dep_id in completed_tasks for dep_id in self.dependencies)

        return time_ready and deps_ready

    def update_next_run_time(self):
        """Update next run time after execution."""
        self.execution_count += 1

        if self.cron_expression:
            # Periodic task: calculate next run time from cron
            self.next_run_time = self._calculate_next_cron_time()
        else:
            # One-time task: no next run
            self.next_run_time = None

    def _calculate_next_cron_time(self, from_time: Optional[float] = None) -> Optional[float]:
        """Calculate next execution time from cron expression.

        Simplified cron format: minute hour day month weekday
        Supports: numbers, *, */n (every n), ranges (1-5), lists (1,3,5)
        """
        if not self.cron_expression:
            return None

        if from_time is None:
            from_time = time.time()

        # Try to use croniter library if available
        try:
            from croniter import croniter
            cron = croniter(self.cron_expression, from_time)
            return cron.get_next()
        except ImportError:
            # Fallback: simple interval-based calculation for */n patterns
            parts = self.cron_expression.split()
            if len(parts) >= 1 and parts[0].startswith('*/'):
                try:
                    interval_minutes = int(parts[0][2:])
                    return from_time + (interval_minutes * 60)
                except ValueError:
                    pass
            # Default: execute once after 1 hour
            return from_time + 3600


@dataclass
class TaskStatistics:
    """Global scheduling state."""
    total_tasks: int = 0
    pending_tasks: int = 0
    running_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    abnormal_tasks: int = 0

    used_cpu: float = 0.0
    used_memory: float = 0.0
    used_cost: float = 0.0
    used_time: float = 0.0
    current_concurrent: int = 0

    avg_wait_time: float = 0.0
    avg_execution_time: float = 0.0
    throughput: float = 0.0

    def update_from_task(self, task: Task):
        """Update state from task."""
        if task.task_status == TaskStatus.INIT:
            self.pending_tasks += 1
        elif task.task_status == TaskStatus.RUNNING:
            self.running_tasks += 1
            self.current_concurrent += 1
        elif task.task_status == TaskStatus.SUCCESS:
            self.completed_tasks += 1
            self.current_concurrent = max(0, self.current_concurrent - 1)
        elif task.task_status == TaskStatus.FAILED:
            self.failed_tasks += 1
            self.current_concurrent = max(0, self.current_concurrent - 1)
        elif task.task_status in (TaskStatus.CANCELLED, TaskStatus.INTERRUPTED, TaskStatus.TIMEOUT):
            self.abnormal_tasks += 1
            self.current_concurrent = max(0, self.current_concurrent - 1)

        self.total_tasks += 1


@dataclass
class ResourceQuota:
    """Resource quota of the machine for task execution."""
    max_cpu: float = 0.0
    max_memory: float = 0.0
    max_time: float = 0.0
    max_concurrent: int = 0

    def is_available(self,
                     used_cpu: float = 0,
                     used_memory: float = 0,
                     used_time: float = 0,
                     current_concurrent: int = 0) -> bool:
        """Check if resources are available."""
        checks = []
        if self.max_cpu > 0:
            checks.append(used_cpu < self.max_cpu)
        if self.max_memory > 0:
            checks.append(used_memory < self.max_memory)
        if self.max_time > 0:
            checks.append(used_time < self.max_time)
        if self.max_concurrent > 0:
            checks.append(current_concurrent < self.max_concurrent)

        return all(checks) if checks else True
