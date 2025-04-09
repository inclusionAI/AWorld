# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

from typing import Any, List, Dict, Union

from aworld.core.singleton import InheritanceSingleton
from aworld.core.task import Task
from aworld.logs.util import logger


class Client(InheritanceSingleton):
    """Submit various tasks in framework for execution and obtain results, when running locally, similar to task."""

    def __init__(self):
        pass

    def submit(self, task: Union[Task, List[Task]] = None, parallel: bool = False, **kwargs) -> Dict[str, Any]:
        """Run the task in local.

        Returns:
            The result of the task execution.
        """
        start = time.time()
        res = {}

        if isinstance(task, Task):
            self._run_in_local(task, res)
        else:
            if parallel:
                loop = self.loop()
                loop.run_until_complete(self._parallel_run_in_local(task, res))
            else:
                for i, t in enumerate(task):
                    self._run_in_local(t, res, i, None)

        res['success'] = True
        res['time_cost'] = time.time() - start
        return res

    async def _parallel_run_in_local(self, task, res):
        with ProcessPoolExecutor() as pool:
            loop = self.loop()
            tasks = [loop.run_in_executor(pool, t.start) for t in task]

            results = await asyncio.gather(*tasks)
            for idx, t in enumerate(results):
                res[f'task_{idx}'] = t

    def loop(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        return loop

    def _run_in_local(self, task: Task, res: Dict[str, Any], idx: int = 0, input: Any = None) -> None:
        try:
            # Execute the task
            if input:
                task.input = input
            result = task.start()
            res[f'task_{idx}'] = result
            return result
        except Exception as e:
            logger.error(traceback.format_exc())
            # Re-raise the exception to allow caller to handle it
            raise e
