# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from threading import Thread
from typing import List, Dict, Any, Union

from aworld.config.conf import TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.context.base import Context
from aworld.core.task import Task
from aworld.logs.util import color_log, Color, trace_logger
from aworld.output import StreamingOutputs
from aworld import trace
from aworld.runners.sequence import SequenceRunner
from aworld.runners.social import SocialRunner
from aworld.trace.server import get_trace_server
from aworld.utils.common import sync_exec

SEQUENCE = "sequence"
SOCIAL = "social"
RUNNERS = {
    SEQUENCE: SequenceRunner,
    SOCIAL: SocialRunner
}


class Runners:
    """Unified entrance to the utility class of the runnable task of execution."""

    @staticmethod
    def streamed_run_task(task: Task, view_trace: bool = False) -> StreamingOutputs:
        """Run the task in stream output."""

        with trace.span(task.name) as span:
            if not task.conf:
                task.conf = TaskConfig()

            streamed_result = StreamingOutputs(
                input=task.input,
                usage={},
                is_complete=False
            )
            task.outputs = streamed_result

            streamed_result._run_impl_task = asyncio.create_task(
                Runners.run_task(task)
            )

        if view_trace:
            thread = Thread(target=get_trace_server().join)
            thread.start()
        return streamed_result

    @staticmethod
    def sync_run_task(task: Union[Task, List[Task]], parallel: bool = False, view_trace: bool = False):
        return sync_exec(Runners.run_task, task=task, parallel=parallel, view_trace=view_trace)

    @staticmethod
    async def run_task(task: Union[Task, List[Task]], parallel: bool = False, view_trace: bool = False):
        """Run tasks for some complex scenarios where agents cannot be directly used.

        Args:
            task: User task define.
            parallel: Whether to process multiple tasks in parallel.
            view_trace: Whether you want to view the trace log.
        """
        import time
        start = time.time()

        if isinstance(task, Task):
            task = [task]

        res = {}
        if parallel:
            await Runners._parallel_run_in_local(task, res)
        else:
            await Runners._run_in_local(task, res)

        usage = Context.instance().token_usage
        color_log(f"task token usage: {usage}",
                  color=Color.pink,
                  logger_=trace_logger)
        res['usage'] = usage
        res['time_cost'] = time.time() - start

        if view_trace:
            thread = Thread(target=get_trace_server().join)
            thread.start()
        return res

    @staticmethod
    def sync_run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = [],
            view_trace: bool = False
    ):
        return sync_exec(
            Runners.run,
            input=input,
            agent=agent,
            swarm=swarm,
            tool_names=tool_names,
            view_trace=view_trace
        )

    @staticmethod
    async def run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = [],
            view_trace: bool = False
    ):
        """Run agent directly with input and tool names.

        Args:
            input: User query.
            agent: An agent with AI model configured, prompts, tools, mcp servers and other agents.
            swarm: Multi-agent topo.
            tool_names: Tool name list.
            view_trace: Whether you want to view the trace log.
        """
        if agent and swarm:
            raise ValueError("`agent` and `swarm` only choose one.")

        if not input:
            raise ValueError('`input` is empty.')

        if agent:
            agent.task = input
            swarm = Swarm(agent)

        task = Task(input=input, swarm=swarm, tool_names=tool_names)

        with trace.span(task.name) as span:
            runner = Runners._choose_runner(task=task)
            res = await runner.run()
            color_log(f"task token usage: {Context.instance().token_usage}",
                      color=Color.pink,
                      logger_=trace_logger)
            trace_logger.info(f"{input} execute finished, response: {res}")

        if view_trace:
            thread = Thread(target=get_trace_server().join)
            thread.start()
        return res

    @staticmethod
    async def _parallel_run_in_local(tasks: List[Task], res):
        # also can use ProcessPoolExecutor
        parallel_tasks = []
        for t in tasks:
            with trace.span(t.name) as span:
                parallel_tasks.append(Runners._choose_runner(task=t).run())

        results = await asyncio.gather(*parallel_tasks)
        for idx, t in enumerate(results):
            res[f'task_{idx}'] = t

    @staticmethod
    async def _run_in_local(tasks: List[Task], res: Dict[str, Any]) -> None:
        for idx, task in enumerate(tasks):
            with trace.span(task.name) as span:
                # Execute the task
                result = await Runners._choose_runner(task=task).run()
                res[f'task_{idx}'] = result

    @staticmethod
    def _choose_runner(task: Task):
        if not task.swarm:
            return SequenceRunner(task=task)

        task.swarm.reset(task.input)
        topology = task.swarm.topology_type
        return RUNNERS.get(topology, SequenceRunner)(task=task)
