# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from typing import List, Dict, Union, AsyncGenerator, Tuple, Any

from aworld.config import RunConfig, EvaluationConfig
from aworld.config.conf import TaskConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Config
from aworld.core.event.base import Message, Constants, TopicType, TaskMessage
from aworld.core.task import Task, TaskResponse, Runner
from aworld.evaluations.base import EvalTask
from aworld.logs.util import logger
from aworld.output import StreamingOutputs
from aworld.runners.evaluate_runner import EvaluateRunner
from aworld.runners.event_runner import TaskEventRunner
from aworld.runners.utils import execute_runner, choose_runners
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

        logger.info(f"start task_id={task.id}, agent={task.agent}, swarm = {task.swarm} ")

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

        logger.debug(f"task_id: {task[0].id} start")
        result = await exec_tasks(task, run_conf)
        logger.debug(f"task_id: {task[0].id} end")
        return result

    @staticmethod
    def sync_run_task(task: Union[Task, List[Task]], run_conf: Config = None) -> Dict[str, TaskResponse]:
        return sync_exec(Runners.run_task, task=task, run_conf=run_conf)

    @staticmethod
    async def streaming_run_task(
            task: Task,
            streaming_mode: str = 'chunk_output',
            run_conf: RunConfig = None
    ) -> AsyncGenerator[Message, None]:
        """Run task with streaming message support.

        Args:
            task: Task to execute.
            streaming_mode: Streaming mode ('chunk_output', 'core', 'all').
            run_conf: Runtime configuration.
            
        Yields:
            Message objects from the streaming queue.
        """
        if not run_conf:
            run_conf = RunConfig()

        # Set up task with streaming mode
        task.streaming_mode = streaming_mode

        # Execute the agent asynchronously
        stream_task = asyncio.create_task(
            Runners.run_task(task, run_conf)
        )
        runners = await choose_runners([task], run_conf=run_conf)
        stream_task = asyncio.create_task(execute_runner(runners, run_conf))
        runner: TaskEventRunner = runners[0]
        streaming_queue = runner.streaming_eventbus
        task_id = task.id

        # Setup end signal
        async def send_end_signal():
            try:
                await stream_task
            except Exception as e:
                logger.error(f"Task execution failed: {e}")
            finally:
                end_message = Message(payload="[END]")
                end_message.context = task.context
                await streaming_queue.publish(end_message)

        asyncio.create_task(send_end_signal())

        def is_task_end_msg(msg: Message):
            return msg and isinstance(msg, Message) and isinstance(msg.payload, str) and msg.payload == "[END]"

        # Receive the messages from the streaming queue
        try:
            while True:
                # Get message from queue (works in both local and distributed mode)
                streaming_msg = await streaming_queue.get_nowait(task_id)

                # End the loop when receiving end signal
                if is_task_end_msg(streaming_msg):
                    break

                yield streaming_msg
        except asyncio.TimeoutError:
            logger.warning(f"Streaming queue timeout for task {task.id}")
        except Exception as e:
            logger.error(f"Error reading from streaming queue: {e}")
            raise
        finally:
            # Clean up queue resources
            await streaming_queue.done(task_id)

    @staticmethod
    def sync_run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = [],
            session_id: str = None,
            run_conf: RunConfig = None
    ) -> TaskResponse:
        return sync_exec(
            Runners.run,
            input=input,
            agent=agent,
            swarm=swarm,
            tool_names=tool_names,
            session_id=session_id,
            run_conf=run_conf
        )

    @staticmethod
    async def run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = [],
            session_id: str = None,
            run_conf: RunConfig = None
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
        res = await Runners.run_task(task, run_conf=run_conf)
        return res.get(task.id)

    @staticmethod
    async def evaluate(task: EvalTask = None,
                       eval_conf: EvaluationConfig = None,
                       run_conf: RunConfig = None):
        # todo: unify in exec_tasks
        runner = EvaluateRunner(task=task, config=eval_conf)
        return await execute_runner([runner], run_conf)
