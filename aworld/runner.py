# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import logging
import uuid
from concurrent.futures.process import ProcessPoolExecutor
from datetime import datetime
from typing import List, Dict, Union, AsyncIterator

from aworld.config import RunConfig
from aworld.config.conf import TaskConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Config
from aworld.core.task import Task, TaskResponse, Runner
from aworld.output import StreamingOutputs
from aworld.utils.common import sync_exec
from aworld.utils.run_util import exec_tasks


class Runners:
    """Unified entrance to the utility class of the runnable task of execution."""

    @staticmethod
    def streamed_run_task(task: Task) -> StreamingOutputs:
        """Run the task in stream output."""
        if not task.conf:
            task.conf = TaskConfig()

        streamed_result = StreamingOutputs(
            input=task.input,
            usage={},
            is_complete=False
        )
        task.outputs = streamed_result
        streamed_result.task_id = task.id

        logging.info(f"[Runners]streamed_run_task start task_id={task.id}, agent={task.agent}, swarm = {task.swarm} ")

        streamed_result._run_impl_task = asyncio.create_task(
            Runners.run_task(task)
        )
        return streamed_result

    @staticmethod
    async def run_task(task: Union[Task, List[Task]], run_conf: RunConfig = None) -> Dict[str, TaskResponse]:
        """Run tasks for some complex scenarios where agents cannot be directly used.

        Args:
            task: User task define.
            run_conf:
        """
        if isinstance(task, Task):
            task = [task]

        logging.debug(f"[Runners]run_task start task_id={task[0].id} start")
        result = await exec_tasks(task, run_conf)
        logging.debug(f"[Runners]run_task end task_id={task[0].id} end")
        return result

    @staticmethod
    def sync_run_task(task: Union[Task, List[Task]], run_conf: Config = None) -> Dict[str, TaskResponse]:
        return sync_exec(Runners.run_task, task=task, run_conf=run_conf)

    @staticmethod
    def sync_run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = [],
            session_id: str = None
    ) -> TaskResponse:
        return sync_exec(
            Runners.run,
            input=input,
            agent=agent,
            swarm=swarm,
            tool_names=tool_names,
            session_id=session_id
        )

    @staticmethod
    async def run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = [],
            session_id: str = None
    ) -> TaskResponse:
        """Run agent directly with input and tool names.

        Args:
            input: User query.
            agent: An agent with AI model configured, prompts, tools, mcp servers and other agents.
            swarm: Multi-agent topo.
            tool_names: Tool name list.
            session_id: Session id.

        Returns:
            TaskResponse: Task response.
        """
        if agent and swarm:
            raise ValueError("`agent` and `swarm` only choose one.")

        if not input:
            raise ValueError('`input` is empty.')

        if agent:
            agent.task = input
            swarm = Swarm(agent)

        task = Task(input=input, swarm=swarm, tool_names=tool_names,
                    event_driven=swarm.event_driven, session_id=session_id)
        res = await Runners.run_task(task)
        return res.get(task.id)

    @staticmethod
    def sync_batch_run(
            agent: Agent = None,
            swarm: Swarm = None,
            input_queries: List[str] = None,
            input_tasks: List[Task] = None,
            batch_size: int = None,
            run_config: RunConfig = None) -> Dict[str, TaskResponse]:
        return sync_exec(
            Runners.batch_run,
            agent=agent,
            swarm=swarm,
            input_queries=input_queries,
            input_tasks=input_tasks,
            batch_size=batch_size,
            run_config=run_config
        )

    @staticmethod
    async def batch_run(
            agent: Agent = None,
            swarm: Swarm = None,
            input_queries: List[str] = None,
            input_tasks: List[Task] = None,
            batch_size: int = None,
            run_config: RunConfig = None) -> Dict[str, TaskResponse]:
        """Build and run tasks in batches.

        Args:
            agent: Agent used to create tasks when `input_queries` is provided.
            input_queries: List of raw inputs to create tasks from.
            input_tasks: Pre-constructed tasks to execute.
            batch_size: Optional batch size for splitting tasks. If not set or <= 0, runs all at once.
            run_config: Runtime configuration settings. If not provided, uses default RunConfig.
        """
        if not input_queries and not input_tasks:
            raise ValueError('input is empty.')
        if input_queries and not agent and not swarm:
            raise ValueError("`agent` and `swarm` cannot both be None.")

        tasks = []
        if input_queries:
            for i in range(len(input_queries)):
                input = input_queries[i]
                task_id = datetime.now().strftime("%Y%m%d%H%M%S") + "_" + str(i) + "_" + str(uuid.uuid4())
                session_id = task_id
                new_swarm = None
                if agent:
                    new_swarm = Swarm(agent.deep_copy())
                else:
                    new_swarm = swarm.deep_copy()
                task = Task(id = task_id, input=input, swarm=new_swarm, session_id=session_id)
                tasks.append(task)
        else:
            tasks = input_tasks

        run_conf = run_config or RunConfig(worker_num=2)
        results: Dict[str, TaskResponse] = {}
        if not batch_size or batch_size <= 0:
            batch_size = len(tasks)

        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            batch_results = await exec_tasks(batch, run_conf)
            results.update(batch_results)
        return results

    @staticmethod
    async def batch_run_stream(
            agent: Agent = None,
            swarm: Swarm = None,
            input_queries: List[str] = None,
            input_tasks: List[Task] = None,
            batch_size: int = None
    ) -> AsyncIterator[Dict[str, TaskResponse]]:
        """Build and run tasks in batches and yield each batch's results as they complete.

        Args:
            agent: Agent used to create tasks when `input_queries` is provided.
            input_queries: List of raw inputs to create tasks from.
            input_tasks: Pre-constructed tasks to execute.
            batch_size: Optional batch size for splitting tasks. If not set or <= 0, runs all at once.
            run_config: Runtime configuration settings. If not provided, uses default RunConfig.

        Yields:
            Dict[str, TaskResponse]: Results for each executed batch.
        """
        if not input_queries and not input_tasks:
            raise ValueError('input is empty.')
        if input_queries and not agent and not swarm:
            raise ValueError("`agent` and `swarm` cannot both be None.")

        tasks: List[Task] = []
        if input_queries:
            for i in range(len(input_queries)):
                input = input_queries[i]
                task_id = datetime.now().strftime("%Y%m%d%H%M%S") + "_" + str(i) + "_" + str(uuid.uuid4())
                session_id = task_id
                new_swarm = None
                if agent:
                    new_swarm = Swarm(agent.deep_copy())
                else:
                    new_swarm = swarm.deep_copy()
                task = Task(id=task_id, input=input, swarm=new_swarm, session_id=session_id)
                tasks.append(task)
        else:
            tasks = input_tasks

        run_conf = RunConfig()
        if not batch_size or batch_size <= 0:
            batch_size = len(tasks)

        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            batch_results = await exec_tasks(batch, run_conf)
            merged_batch: Dict[str, TaskResponse] = {}
            for br in batch_results:
                merged_batch.update(br)
            yield merged_batch