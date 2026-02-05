# coding: utf-8
# Copyright (c) inclusionAI.
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Literal

from aworld.core.common import TaskStatus, TaskStatusValue
from aworld.core.task import Task

ScheduleStrategyType = Literal['auto', 'dag', 'fifo', 'priority', 'resource']


@dataclass
class ScheduledTask(Task):
    """Task with scheduling metadata."""
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
        if not isinstance(other, ScheduledTask):
            return NotImplemented
        return self.priority < other.priority

    @property
    def task_id(self) -> str:
        return self.id

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
class ScheduledTaskStatistics:
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

    def update_from_task(self, task: ScheduledTask):
        """Update state from task."""
        if task.status == TaskStatusValue.INIT:
            self.pending_tasks += 1
        elif task.status == TaskStatusValue.RUNNING:
            self.running_tasks += 1
            self.current_concurrent += 1
        elif task.status == TaskStatusValue.SUCCESS:
            self.completed_tasks += 1
            self.current_concurrent = max(0, self.current_concurrent - 1)
        elif task.status == TaskStatusValue.FAILED:
            self.failed_tasks += 1
            self.current_concurrent = max(0, self.current_concurrent - 1)
        elif task.status in (TaskStatusValue.CANCELLED, TaskStatusValue.INTERRUPTED, TaskStatusValue.TIMEOUT):
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
