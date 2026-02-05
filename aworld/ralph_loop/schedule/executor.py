# coding: utf-8
# Copyright (c) inclusionAI.
import time
from typing import List, Dict, Any, Optional

from aworld.config import RunConfig
from aworld.logs.util import logger
from aworld.runners.runtime_engine import RuntimeEngine
from aworld.ralph_loop.schedule.types import ScheduledTask
from aworld.utils.run_util import exec_tasks


class RuntimeEngineExecutor:
    """Wrapper for RuntimeEngine to execute scheduled tasks, provides execution and status management for each task."""

    def __init__(self, runtime_engine: Optional[RuntimeEngine] = None, run_config: Optional[RunConfig] = None):
        self.runtime_engine = runtime_engine
        self.run_config = run_config

    async def execute(self, tasks: List[ScheduledTask]) -> Dict[str, Any]:
        """Execute a list of scheduled tasks using the runtime engine, updates task status and start time before execution.

        Args:
            tasks: Schedule task list.

        Returns:
            Dictionary of results.
        """
        if not tasks:
            return {}

        for task in tasks:
            task.task_status = 'running'
            task.started_at = time.time()

        funcs = []
        # Wrap each task in an async executor function
        for scheduled_task in tasks:
            async def task_execute(st=scheduled_task):
                return await self._one_task_execute(st)

            funcs.append(task_execute)

        results = await self.runtime_engine.execute(funcs)
        logger.info(f"{self.runtime_engine.name} execute {len(tasks)} tasks finished")
        return results

    async def _one_task_execute(self, task: ScheduledTask) -> Any:
        """Execute a single scheduled task, update its status, and handle exceptions."""
        try:
            task.task_status = 'running'
            task.started_at = time.time()

            results = await exec_tasks(tasks=[task])

            result = results.get(task.id)
            task.result = result
            task.task_status = 'success'
            task.completed_at = time.time()

            return result
        except Exception as e:
            task.task_status = 'failed'
            task.error = str(e)
            task.completed_at = time.time()
            logger.error(f"Task {task.task_id} execution failed: {e}")
