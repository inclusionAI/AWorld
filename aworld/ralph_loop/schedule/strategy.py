# coding: utf-8
# Copyright (c) inclusionAI.
import heapq
from abc import abstractmethod, ABC
from collections import deque
from typing import List, Optional, Dict, Type

from aworld.logs.util import logger
from aworld.ralph_loop.schedule.types import ScheduledTask, ScheduleStrategyType, ResourceQuota


class ScheduleStrategy(ABC):
    """Abstract base class for scheduling strategies."""

    @abstractmethod
    async def schedule(self, tasks: List[ScheduledTask], **kwargs) -> List[List[ScheduledTask]]:
        """
        Schedule tasks and return execution batches.

        Returns:
            List of task batches, where each batch can be executed in parallel.
        """
        pass


class FIFOStrategy(ScheduleStrategy):
    """First-In-First-Out scheduling strategy."""

    def __init__(self, batch_size: int = 10, **kwargs):
        self.batch_size = batch_size

    async def schedule(self, tasks: List[ScheduledTask], **kwargs) -> List[List[ScheduledTask]]:
        """Schedule tasks in FIFO order."""
        queue = FIFOTaskQueue()
        for task in tasks:
            queue.push(task)

        batches = []
        current_batch = []

        while not queue.is_empty():
            task = queue.pop()
            if task:
                current_batch.append(task)

            if len(current_batch) >= self.batch_size:
                batches.append(current_batch)
                current_batch = []

        if current_batch:
            batches.append(current_batch)

        return batches


class PriorityStrategy(ScheduleStrategy):
    """Priority-based scheduling strategy."""

    def __init__(self, batch_size: int = 10, **kwargs):
        self.batch_size = batch_size

    async def schedule(self, tasks: List[ScheduledTask], **kwargs) -> List[List[ScheduledTask]]:
        """Schedule tasks by priority."""
        queue = PriorityTaskQueue()
        for task in tasks:
            queue.push(task)

        batches = []
        current_batch = []

        while not queue.is_empty():
            task = queue.pop()
            if task:
                current_batch.append(task)

            if len(current_batch) >= self.batch_size:
                batches.append(current_batch)
                current_batch = []

        if current_batch:
            batches.append(current_batch)

        return batches


class DAGStrategy(ScheduleStrategy):
    """DAG-based scheduling strategy (topological sort)."""

    async def schedule(self, tasks: List[ScheduledTask], **kwargs) -> List[List[ScheduledTask]]:
        """Schedule tasks based on DAG dependencies."""
        dag = TaskDAG()

        dag.add_task(tasks)

        valid, error = TaskDAG.validate_dag(dag)
        if not valid:
            logger.error(f"DAG validation failed: {error}")
            return [[]]

        execution_levels = dag.get_execution_order()
        task_map = {task.task_id: task for task in tasks}

        batches = []
        for level in execution_levels:
            batch = [task_map[task_id] for task_id in level if task_id in task_map]
            if batch:
                batches.append(batch)

        return batches


class AutoStrategy(ScheduleStrategy):
    """Adaptive scheduling strategy that switches based on task conditions."""

    def __init__(self, batch_size: int = 10):
        self.dag_strategy = DAGStrategy()
        self.priority_strategy = PriorityStrategy(batch_size=batch_size)
        self.fifo_strategy = FIFOStrategy(batch_size=batch_size)

    async def schedule(self, tasks: List[ScheduledTask], **kwargs) -> List[List[ScheduledTask]]:
        """Adaptively choose scheduling strategy.

        Args:
            tasks: Scheduled task list.
        """

        # check dependencies first
        if any(task.dependencies for task in tasks):
            logger.info("Using DAG strategy, tasks have dependencies")
            return await self.dag_strategy.schedule(tasks, **kwargs)

        # check task priority
        priority_counts = {}
        for task in tasks:
            priority_counts[task.priority] = priority_counts.get(task.priority, 0) + 1

        if len(priority_counts) > 1:
            logger.info("Using Priority strategy, tasks varied priorities")
            return await self.priority_strategy.schedule(tasks, **kwargs)

        # FIFO
        logger.info("Using FIFO strategy")
        return await self.fifo_strategy.schedule(tasks, **kwargs)


STRATEGY_MAP: Dict[ScheduleStrategyType, Type[ScheduleStrategy]] = {
    "auto": AutoStrategy,
    "dag": DAGStrategy,
    "priority": PriorityStrategy,
    "fifo": FIFOStrategy,
}


def register_strategy(strategy_type: ScheduleStrategyType, strategy: Type[ScheduleStrategy]) -> bool:
    """Register a new strategy.

    Args:
        strategy_type: Type of schedule strategy.
        strategy: Class of ScheduleStrategy
    """
    STRATEGY_MAP[strategy_type] = strategy
    return True


def create_strategy(strategy_type: Optional[ScheduleStrategyType] = None, **kwargs) -> ScheduleStrategy:
    """Create strategy instance."""
    return STRATEGY_MAP.get(strategy_type, AutoStrategy())(**kwargs)


class TaskQueue(ABC):
    """Base class for task queues, simple API."""

    @abstractmethod
    def push(self, task: ScheduledTask):
        """Add a task to the queue."""
        pass

    @abstractmethod
    def pop(self) -> Optional[ScheduledTask]:
        """Remove and return the next task."""
        pass

    @abstractmethod
    def peek(self) -> Optional[ScheduledTask]:
        """Return the next task without removing it."""
        pass

    @abstractmethod
    def size(self) -> int:
        """Return the number of tasks in the queue."""
        pass

    @abstractmethod
    def is_empty(self) -> bool:
        """Check if queue is empty."""
        pass

    @abstractmethod
    def clear(self):
        """Clear all tasks from the queue."""
        pass


class FIFOTaskQueue(TaskQueue):
    """First in first out queue."""

    def __init__(self):
        self.queue = deque()

    def push(self, task: ScheduledTask):
        self.queue.append(task)

    def pop(self) -> Optional[ScheduledTask]:
        if self.queue:
            return self.queue.popleft()
        return None

    def peek(self) -> Optional[ScheduledTask]:
        if self.queue:
            return self.queue[0]
        return None

    def size(self) -> int:
        return len(self.queue)

    def is_empty(self) -> bool:
        return len(self.queue) == 0

    def clear(self):
        self.queue.clear()


class PriorityTaskQueue(TaskQueue):
    """Priority-based queue using heap."""

    def __init__(self):
        self.heap: List[tuple] = []
        self.counter = 0

    def push(self, task: ScheduledTask):
        # (priority_value, counter, task)
        priority_value = task.priority
        heapq.heappush(self.heap, (priority_value, self.counter, task))
        self.counter += 1

    def pop(self) -> Optional[ScheduledTask]:
        if self.heap:
            _, _, task = heapq.heappop(self.heap)
            return task
        return None

    def peek(self) -> Optional[ScheduledTask]:
        if self.heap:
            _, _, task = self.heap[0]
            return task
        return None

    def size(self) -> int:
        return len(self.heap)

    def is_empty(self) -> bool:
        return len(self.heap) == 0

    def clear(self):
        self.heap.clear()
        self.counter = 0

    def update_priority(self, task_id: str, new_priority: int) -> None:
        """Update the task priority (rebuild heap).

        Args:
            task_id: Task id.
            new_priority: The priority of the task.
        """
        tasks = [task for _, _, task in self.heap if task.task_id != task_id]
        task_to_update = None
        for _, _, task in self.heap:
            if task.task_id == task_id:
                task_to_update = task
                break

        self.clear()
        for task in tasks:
            self.push(task)

        if task_to_update:
            task_to_update.priority = new_priority
            self.push(task_to_update)


class ResourceAwareTaskQueue(TaskQueue):
    """Resource-aware queue that considers resource constraints."""

    def __init__(self, resource_quota: ResourceQuota):
        self.priority_queue = PriorityTaskQueue()
        self.resource_quota = resource_quota
        self.pending_tasks: List[ScheduledTask] = []

    def push(self, task: ScheduledTask):
        """Add task considering resource requirements."""
        self.priority_queue.push(task)

    def pop(self) -> Optional[ScheduledTask]:
        """Pop task that fits resource constraints."""
        temp_tasks = []

        while not self.priority_queue.is_empty():
            task = self.priority_queue.pop()

            if self._can_allocate(task):
                for t in temp_tasks:
                    self.priority_queue.push(t)
                return task
            else:
                temp_tasks.append(task)

        for t in temp_tasks:
            self.priority_queue.push(t)

        return None

    def peek(self) -> Optional[ScheduledTask]:
        return self.priority_queue.peek()

    def size(self) -> int:
        return self.priority_queue.size()

    def is_empty(self) -> bool:
        return self.priority_queue.is_empty()

    def clear(self):
        self.priority_queue.clear()
        self.pending_tasks.clear()

    def _can_allocate(self, task: ScheduledTask) -> bool:
        """Check if task can be allocated given current resources."""
        # TODO: based global resource of cluster
        return True

    def update_resource_quota(self, quota: ResourceQuota):
        """Update resource quota."""
        self.resource_quota = quota
