# coding: utf-8
# Copyright (c) inclusionAI.
import time
from typing import List, Dict, Any, Optional

from aworld.config import RunConfig
from aworld.core.common import TaskStatus
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.runners.runtime_engine import RuntimeEngine
from aworld.runners.utils import runtime_engine
from aworld.utils.common import sync_exec
from aworld.utils.run_util import exec_tasks


class RuntimeEngineExecutor:
    """Wrapper for RuntimeEngine to execute scheduled tasks, provides execution and status management for each task."""

    def __init__(self, engine: Optional[RuntimeEngine] = None, run_config: Optional[RunConfig] = None):
        self.runtime_engine = engine or sync_exec(runtime_engine, run_config)

    async def execute(self, tasks: List[Task]) -> Dict[str, Any]:
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
                st.task_status = res.get(st.id).status
                st.completed_at = time.time()
                return res

            funcs.append(task_execute)

        results = await self.runtime_engine.execute(funcs)
        logger.info(f"{self.runtime_engine.name} execute {len(tasks)} tasks finished")
        return results
