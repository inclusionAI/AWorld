# coding: utf-8
# Copyright (c) inclusionAI.
from collections import deque
from typing import Dict, List, Set, Optional

from aworld.core.common import TaskStatus
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.schedule.types import SchedulableTask


class TaskGraph:
    """Directed Acyclic Graph for task scheduling."""

    def __init__(self):
        self.nodes: Dict[str, Task] = {}
        self.completed: Set[str] = set()
        # predecessor[task_id] = set of tasks that must complete before task_id
        self.predecessor: Dict[str, Set[str]] = {}
        # successor[task_id] = set of tasks that depend on task_id
        self.successor: Dict[str, Set[str]] = {}

    def add_tasks(self, tasks: List[Task]):
        for task in tasks:
            self.add_task(task)

    def add_task(self, task: Task):
        """Add a task to the Graph."""
        if task.id in self.nodes:
            logger.warning(f"Task {task.id} already exists in DAG")
            return

        self.nodes[task.id] = task

        if task.id not in self.predecessor:
            self.predecessor[task.id] = set()
        if task.id not in self.successor:
            self.successor[task.id] = set()

        if hasattr(task, "dependencies"):
            for dep_id in task.dependencies:
                self.predecessor[task.id].add(dep_id)

                if dep_id not in self.successor:
                    self.successor[dep_id] = set()
                self.successor[dep_id].add(task.id)

                if dep_id not in self.nodes and dep_id not in self.completed:
                    logger.warning(f"Dependency {dep_id} not found for task {task.id}")

    def get_ready_tasks(self) -> List[Task]:
        """Get all tasks that are ready to execute (no pending dependencies)."""
        ready_tasks = []
        for task_id, node in self.nodes.items():
            if node.task_status == TaskStatus.INIT:
                if task_id in self.predecessor:
                    pending_predecessors = self.predecessor[task_id] - self.completed
                    if not pending_predecessors:
                        ready_tasks.append(node)
                else:
                    ready_tasks.append(node)
        return ready_tasks

    def mark_completed(self, task_id: str):
        """Mark a task as completed and update dependent tasks."""
        if task_id not in self.nodes:
            logger.warning(f"Task {task_id} not found in DAG")
            return

        self.completed.add(task_id)

        if task_id in self.predecessor:
            del self.predecessor[task_id]
        if task_id in self.successor:
            del self.successor[task_id]

        del self.nodes[task_id]

    def mark_failed(self, task_id: str):
        """Mark a task as failed and handle dependents."""
        if task_id not in self.nodes:
            return

        if task_id in self.successor:
            for dependent_id in self.successor[task_id]:
                if dependent_id in self.nodes:
                    self.nodes[dependent_id].task_status = TaskStatus.FAILED

        if task_id in self.predecessor:
            del self.predecessor[task_id]
        if task_id in self.successor:
            del self.successor[task_id]

        del self.nodes[task_id]

    def get_execution_order(self) -> List[List[str]]:
        """Get topological execution order (by levels) using Kahn's algorithm."""

        in_degree = {}
        for task_id in self.nodes:
            if task_id in self.predecessor:
                pending = self.predecessor[task_id] - self.completed
                in_degree[task_id] = len(pending)
            else:
                in_degree[task_id] = 0

        queue = deque([task_id for task_id, degree in in_degree.items() if degree == 0])
        levels = []

        while queue:
            current_level = list(queue)
            levels.append(current_level)
            queue.clear()

            for task_id in current_level:
                if task_id not in self.successor:
                    continue

                for successor_id in self.successor[task_id]:
                    if successor_id in in_degree:
                        in_degree[successor_id] -= 1
                        if in_degree[successor_id] == 0:
                            queue.append(successor_id)

        return levels

    def has_cycle(self) -> bool:
        """Check if DAG has a cycle using DFS."""
        visited = set()
        rec_stack = set()

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            if node_id in self.successor:
                for successor_id in self.successor[node_id]:
                    if successor_id not in visited:
                        if dfs(successor_id):
                            return True
                    elif successor_id in rec_stack:
                        return True

            rec_stack.remove(node_id)
            return False

        for task_id in self.nodes:
            if task_id not in visited:
                if dfs(task_id):
                    return True

        return False

    def get_critical_path(self) -> List[str]:
        """Get critical path (longest path) in DAG."""
        longest_path = {}
        path_to = {}

        def calculate_longest_path(task_id: str) -> int:
            if task_id in longest_path:
                return longest_path[task_id]

            if task_id not in self.nodes:
                longest_path[task_id] = 0
                return 0

            if task_id not in self.successor or not self.successor[task_id]:
                longest_path[task_id] = 1
                return 1

            max_length = 0
            next_task = None
            for successor_id in self.successor[task_id]:
                length = calculate_longest_path(successor_id)
                if length > max_length:
                    max_length = length
                    next_task = successor_id

            longest_path[task_id] = max_length + 1
            if next_task:
                path_to[task_id] = next_task
            return longest_path[task_id]

        for task_id in self.nodes:
            calculate_longest_path(task_id)

        if not longest_path:
            return []

        start = max(longest_path, key=longest_path.get)

        path = [start]
        current = start
        while current in path_to:
            current = path_to[current]
            path.append(current)

        return path

    def in_degree(self) -> Dict[str, int]:
        in_degree = {}
        for k, _ in self.nodes.items():
            tasks = self.predecessor[k]
            in_degree[k] = len(tasks)
        return in_degree

    def out_degree(self) -> Dict[str, int]:
        out_degree = {}
        for k, _ in self.nodes.items():
            tasks = self.successor[k]
            out_degree[k] = len(tasks)
        return out_degree

    def get_statistics(self) -> Dict:
        ready_count = 0
        for task_id in self.nodes:
            if task_id in self.predecessor:
                pending = self.predecessor[task_id] - self.completed
                if not pending:
                    ready_count += 1
            else:
                ready_count += 1

        return {
            "total_tasks": len(self.nodes),
            "completed_tasks": len(self.completed),
            "pending_tasks": sum(1 for n in self.nodes.values() if n.task_status == TaskStatus.INIT),
            "ready_tasks": ready_count,
            "max_depth": len(self.get_execution_order()),
            "has_cycle": self.has_cycle(),
        }

    @staticmethod
    def validate_dag(dag: 'TaskGraph') -> tuple[bool, Optional[str]]:
        """Validate DAG structure."""
        # Check for cycles
        if dag.has_cycle():
            return False, "DAG contains cycles"

        # Check for orphaned dependencies
        all_task_ids = set(dag.nodes.keys()) | dag.completed
        for task_id in dag.nodes:
            if task_id in dag.predecessor:
                for dep_id in dag.predecessor[task_id]:
                    if dep_id not in all_task_ids:
                        return False, f"Task {task_id} has undefined dependency {dep_id}"

        return True, None

    @staticmethod
    def validate_task_dependencies(task: SchedulableTask, existing_tasks: Set[str]) -> tuple[bool, Optional[str]]:
        """Validate task dependencies before adding to DAG."""
        for dep_id in task.dependencies:
            if dep_id == task.id:
                return False, "Task cannot depend on itself"

            if dep_id not in existing_tasks:
                logger.warning(f"Dependency {dep_id} not found in existing tasks")

        return True, None
