# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from typing import List, Dict, Union

from aworld.config.conf import TaskConfig, ConfigDict
from aworld.core.agent.llm_agent import Agent
from aworld.core.agent.swarm import Swarm, SEQUENCE, SEQUENCE_EVENT, SOCIAL, SOCIAL_EVENT
from aworld.core.common import Config
from aworld.core.runtime_engine import LOCAL
from aworld.core.task import Task, TaskResponse
from aworld.output import StreamingOutputs
from aworld import trace
from aworld.runners.call_driven_runner import SequenceRunner, SocialRunner
from aworld.runners.sequence import SequenceRunner as SequenceEventRunner
from aworld.runners.social import SocialRunner as SocialEventRunner
from aworld.runners.utils import choose_runner
from aworld.utils.common import sync_exec, new_instance, snake_to_camel

RUNNERS = {
    SEQUENCE: SequenceRunner,
    SOCIAL: SocialRunner,
    SEQUENCE_EVENT: SequenceEventRunner,
    SOCIAL_EVENT: SocialEventRunner
}


class Runners:
    """Unified entrance to the utility class of the runnable task of execution."""

    @staticmethod
    def streamed_run_task(task: Task) -> StreamingOutputs:
        """Run the task in stream output."""

        with trace.span(f"streamed_{task.name}") as span:
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
        with trace.span('run_task') as span:
            if not run_conf:
                run_conf = ConfigDict({"name": LOCAL})

            name = run_conf.name
            if run_conf.get('cls'):
                runtime_backend = new_instance(run_conf.cls, run_conf)
            else:
                runtime_backend = new_instance(
                    f"aworld.core.runtime_backend.{snake_to_camel(name)}Runtime", run_conf)
            runtime_engine = runtime_backend.build_engine()

            if isinstance(task, Task):
                task = [task]
            return await runtime_engine.execute([choose_runner(t).run for t in task])

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
