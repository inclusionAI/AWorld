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
from aworld.runners.utils import execute_runner
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
            streaming_conf: Dict[str, Any] = None,
            run_conf: RunConfig = None
    ) -> AsyncGenerator[Message, None]:
        """Run task with streaming message support.
        
        Supports both single-process (inmemory) and distributed (redis/rabbitmq) scenarios.
        The queue backend is determined by run_conf.streaming_queue_config.
        
        Args:
            task: Task to execute.
            streaming_mode: Streaming mode ('chunk_output', 'core', 'custom', 'all').
            streaming_conf: Custom streaming configuration.
            run_conf: Runtime configuration including streaming_queue_config.
            
        Yields:
            Message objects from the streaming queue.
            
        Example:
            # Local mode (default)
            async for msg in Runners.streaming_run_task(task):
                print(msg)
            
            # Distributed mode with Redis
            run_conf = RunConfig(streaming_queue_config={
                'backend': 'redis',
                'redis': {'host': 'localhost', 'port': 6379}
            })
            async for msg in Runners.streaming_run_task(task, run_conf=run_conf):
                print(msg)
        """
        import uuid
        from aworld.core.streaming_queue import (
            build_streaming_queue, 
            StreamingQueueConfig,
            InMemoryStreamingQueue
        )
        
        if not run_conf:
            run_conf = RunConfig()
        
        # Build streaming queue based on configuration
        if run_conf.streaming_queue_config:
            # Distributed mode: use configured backend
            queue_config_dict = run_conf.streaming_queue_config.copy()
            if 'queue_id' not in queue_config_dict:
                queue_config_dict['queue_id'] = f"task-{task.id}-{uuid.uuid4().hex[:8]}"
            
            queue_config = StreamingQueueConfig(**queue_config_dict)
            queue_provider = build_streaming_queue(queue_config)
            logger.info(f"Using {queue_config.backend} streaming queue: {queue_provider.get_queue_id()}")
        else:
            # Local mode: use in-memory queue
            queue_config_dict = {
                'backend': 'inmemory',
                'queue_id': f"task-{task.id}"
            }
            queue_config = StreamingQueueConfig(**queue_config_dict)
            queue_provider = InMemoryStreamingQueue(queue_config)
            logger.debug(f"Using in-memory streaming queue for task {task.id}")

        # Set up task with streaming queue
        task.streaming_queue_provider = queue_provider
        task.streaming_queue_id = queue_provider.get_queue_id()
        task.streaming_queue_config = queue_config_dict  # Store config for reconstruction in distributed scenarios
        task.streaming_mode = streaming_mode
        
        if streaming_mode == 'custom':
            task.streaming_config = streaming_conf

        # Execute the agent asynchronously
        stream_task = asyncio.create_task(
            Runners.run_task(task, run_conf)
        )

        # Setup end signal
        async def send_end_signal():
            try:
                await stream_task
            except Exception as e:
                logger.error(f"Task execution failed: {e}")
            finally:
                await queue_provider.put(Message(payload="[END]"))

        asyncio.create_task(send_end_signal())

        def is_task_end_msg(msg: Message):
            return msg and isinstance(msg, Message) and isinstance(msg.payload, str) and msg.payload == "[END]"

        # Receive the messages from the streaming queue
        try:
            while True:
                # Get message from queue (works in both local and distributed mode)
                streaming_msg = await queue_provider.get(
                    timeout=run_conf.streaming_queue_config.get('timeout', 60) if run_conf.streaming_queue_config else 60)

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
            await queue_provider.close()

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
