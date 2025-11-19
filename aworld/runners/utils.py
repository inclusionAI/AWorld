# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import List, Dict, Optional
from contextlib import asynccontextmanager
import uuid

from aworld.config import RunConfig, EngineName, ConfigDict, TaskConfig
from aworld.core.agent.swarm import GraphBuildType
from aworld.core.event.base import Message, Constants

from aworld.core.task import Task, TaskResponse, Runner
from aworld.logs.util import logger
from aworld.runners.task_runner import TaskRunner
from aworld.utils.common import new_instance, snake_to_camel


async def choose_runners(tasks: List[Task], agent_oriented: bool = True) -> List[Runner]:
    """Choose the correct runner to run the task.

    Args:
        tasks: A list of tasks that contains agents, tools and datas.
        agent_oriented: Whether the runner is agent-oriented.

    Returns:
        Runner instance or exception.
    """
    runners = []
    for task in tasks:
        # user custom runner class
        runner_cls = task.runner_cls
        if runner_cls:
            return new_instance(runner_cls, task)
        else:
            # user runner class in the framework
            if task.swarm:
                task.swarm.event_driven = task.event_driven
                execute_type = task.swarm.build_type
            else:
                execute_type = GraphBuildType.WORKFLOW.value

            if task.event_driven:
                runner = new_instance("aworld.runners.event_runner.TaskEventRunner",
                                      task,
                                      agent_oriented=agent_oriented)
            else:
                runner = new_instance(
                    f"aworld.runners.call_driven_runner.{snake_to_camel(execute_type)}Runner",
                    task
                )
        runners.append(runner)
    return runners


async def execute_runner(runners: List[Runner], run_conf: RunConfig) -> Dict[str, TaskResponse]:
    """Execute runner in the runtime engine.

    Args:
        runners: The task processing flow.
        run_conf: Runtime config, can choose the special computing engine to execute the runner.
    """
    if not run_conf:
        run_conf = RunConfig()

    name = run_conf.engine_name
    if run_conf.cls:
        runtime_backend = new_instance(run_conf.cls, run_conf)
    else:
        runtime_backend = new_instance(
            f"aworld.runners.runtime_engine.{snake_to_camel(name)}Runtime", run_conf)
    runtime_engine = runtime_backend.build_engine()

    if run_conf.engine_name != EngineName.LOCAL or run_conf.reuse_process == False:
        # distributed in AWorld, the `context` can't carry by response
        for runner in runners:
            if not isinstance(runner, TaskRunner):
                logger.info("not task runner in AWorld, skip...")
                continue
            if runner.task.conf:
                runner.task.conf.resp_carry_context = False
            else:
                runner.task.conf = ConfigDict(TaskConfig(resp_carry_context=False).model_dump())
    return await runtime_engine.execute([runner.run for runner in runners])


def endless_detect(records: List[str], endless_threshold: int, root_agent_name: str):
    """A very simple implementation of endless loop detection.

    Args:
        records: Call sequence of agent.
        endless_threshold: Threshold for the number of repetitions.
        root_agent_name: Name of the entrance agent.
    """
    if not records:
        return False

    threshold = endless_threshold
    last_agent_name = root_agent_name
    count = 1
    for i in range(len(records) - 2, -1, -1):
        if last_agent_name == records[i]:
            count += 1
        else:
            last_agent_name = records[i]
            count = 1

        if count >= threshold:
            logger.warning("detect loop, will exit the loop.")
            return True

    if len(records) > 6:
        last_agent_name = None
        # latest
        for j in range(1, 3):
            for i in range(len(records) - j, 0, -2):
                if last_agent_name and last_agent_name == (records[i], records[i - 1]):
                    count += 1
                elif last_agent_name is None:
                    last_agent_name = (records[i], records[i - 1])
                    count = 1
                else:
                    last_agent_name = None
                    break

                if count >= threshold:
                    logger.warning(f"detect loop: {last_agent_name}, will exit the loop.")
                    return True

    return False


async def long_wait_message_state(message: Message):
    from aworld.runners.state_manager import HandleResult, RunNodeBusiType
    from aworld.runners.state_manager import RuntimeStateManager, RunNodeStatus

    state_mng = RuntimeStateManager.instance()
    msg_id = message.id
    # init node
    state_mng.create_node(
        node_id=msg_id,
        busi_type=RunNodeBusiType.from_message_category(message.category),
        busi_id=message.receiver or "",
        session_id=message.session_id,
        task_id=message.task_id,
        msg_id=msg_id,
        msg_from=message.sender)
    # wait for message node completion
    res_node = await state_mng.wait_for_node_completion(node_id=msg_id)
    if res_node.status == RunNodeStatus.SUCCESS or res_node.results:
        # get result and status from node
        if not res_node or not res_node.results:
            return None
        handle_result: HandleResult = res_node.results[0]
        logger.info(f"long_wait_message_state|origin result: {handle_result}")
        return handle_result.result.payload
    else:
        logger.debug(f"long_wait_message_state|failed with node: {res_node}.")
        raise ValueError(f"long_wait_message_state|failed with node: {res_node}")


@asynccontextmanager
async def managed_runtime_node(
    context,
    busi_type,
    busi_id: str = "",
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    node_id: Optional[str] = None,
    parent_node_id: Optional[str] = None,
    msg_id: Optional[str] = None,
    msg_from: Optional[str] = None,
    group_id: Optional[str] = None,
    sub_group_root_id: Optional[str] = None,
    metadata: Optional[dict] = None
):
    """Context manager for creating, running, and managing runtime node states.
    
    Args:
        context: Message context object containing session_id and task_id
        busi_type: Business type (RunNodeBusiType)
        busi_id: Business ID, defaults to empty string
        session_id: Session ID, if provided will be used preferentially, otherwise obtained from context
        task_id: Task ID, if provided will be used preferentially, otherwise obtained from context
        node_id: Node ID, if not provided will be auto-generated as UUID
        parent_node_id: Parent node ID, used to establish node hierarchy
        msg_id: Message ID, associated message ID
        msg_from: Message sender
        group_id: Group ID
        sub_group_root_id: Sub-group root node ID
        metadata: Metadata dictionary
    
    Yields:
        node: Created RunNode object, returns if creation succeeds, otherwise returns None
    
    Example:
        async with managed_runtime_node(
            context=message.context,
            busi_type=RunNodeBusiType.LLM,
            busi_id="",
            parent_node_id=message.id,
            msg_id=message.id
        ) as node:
            # Execute operation
            result = await some_operation()
            # If operation succeeds, context manager will automatically call run_succeed
            # If exception occurs, will automatically call run_failed
    """
    from aworld.runners.state_manager import RuntimeStateManager
    
    state_manager = RuntimeStateManager.instance()
    
    # Get session_id and task_id, prioritize passed parameters, otherwise get from context
    current_session_id = session_id
    current_task_id = task_id
    
    if context:
        if current_session_id is None and hasattr(context, 'session_id'):
            current_session_id = context.session_id
        if current_task_id is None and hasattr(context, 'task_id'):
            current_task_id = context.task_id
    
    # Create node
    node = state_manager.create_node(
        node_id=node_id or str(uuid.uuid4()),
        busi_type=busi_type,
        busi_id=busi_id,
        session_id=current_session_id or "",
        task_id=current_task_id,
        parent_node_id=parent_node_id,
        msg_id=msg_id,
        msg_from=msg_from,
        group_id=group_id,
        sub_group_root_id=sub_group_root_id,
        metadata=metadata
    )
    
    # If node creation succeeds, start running
    if node and hasattr(node, 'node_id'):
        state_manager.run_node(node.node_id)
    
    try:
        yield node
        # If execution succeeds, mark as success
        if node and hasattr(node, 'node_id'):
            state_manager.run_succeed(node.node_id)
    except Exception:
        # If exception occurs, mark as failed
        if node and hasattr(node, 'node_id'):
            state_manager.run_failed(node.node_id)
        raise

