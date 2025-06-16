# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import os
import datetime
import json
from typing import List, Dict, Any, Union

from aworld.config.conf import TaskConfig
from aworld.core.agent.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Config
from aworld.core.task import Task, TaskResponse, Runner
from aworld.output import StreamingOutputs
from aworld.replay_buffer.processor import ReplayBufferSpanExporter
from aworld.runners.utils import choose_runners, execute_runner
from aworld.utils.common import sync_exec
from aworld.trace.base import get_tracer_provider_silent


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

        streamed_result._run_impl_task = asyncio.create_task(
            Runners.run_task(task)
        )
        return streamed_result

    @staticmethod
    async def run_task(task: Union[Task, List[Task]], run_conf: Config = None) -> Dict[str, TaskResponse]:
        """Run tasks for some complex scenarios where agents cannot be directly used.

        Args:
            task: User task define.
            run_conf:
        """
        if isinstance(task, Task):
            task = [task]

            runners: List[Runner] = await choose_runners(task)
            res = await execute_runner(runners, run_conf)
            Runners._output_replay_buffer()
            return res

    @staticmethod
    def sync_run_task(task: Union[Task, List[Task]], run_conf: Config = None) -> Dict[str, TaskResponse]:
        return sync_exec(Runners.run_task, task=task, run_conf=run_conf)

    @staticmethod
    def sync_run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = []
    ) -> TaskResponse:
        return sync_exec(
            Runners.run,
            input=input,
            agent=agent,
            swarm=swarm,
            tool_names=tool_names
        )

    @staticmethod
    async def run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = []
    ) -> TaskResponse:
        """Run agent directly with input and tool names.

        Args:
            input: User query.
            agent: An agent with AI model configured, prompts, tools, mcp servers and other agents.
            swarm: Multi-agent topo.
            tool_names: Tool name list.
        """
        if agent and swarm:
            raise ValueError("`agent` and `swarm` only choose one.")

        if not input:
            raise ValueError('`input` is empty.')

        if agent:
            agent.task = input
            swarm = Swarm(agent)

        task = Task(input=input, swarm=swarm, tool_names=tool_names, event_driven=swarm.event_driven)
        res = await Runners.run_task(task)
        return res.get(task.id)

    @staticmethod
    def _output_replay_buffer():
        trace_provider = get_tracer_provider_silent()
        if trace_provider:
            trace_provider.force_flush()
        replay_dir = os.path.join("./", "trace_data", "replay_buffer")
        replay_dataset_path = os.getenv("REPLAY_TRACE_DATASET_PATH", replay_dir)
        output_dir = os.path.abspath(replay_dataset_path)
        ReplayBufferSpanExporter.write_replay_buffer(output_dir)
