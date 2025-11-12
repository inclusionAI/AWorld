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
        task: A task that contains agents, tools and datas.

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
    """上下文管理器，用于创建、运行和管理运行时节点的状态。
    
    Args:
        context: 消息上下文对象，包含session_id和task_id
        busi_type: 业务类型 (RunNodeBusiType)
        busi_id: 业务ID，默认为空字符串
        session_id: 会话ID，如果提供则优先使用，否则从context获取
        task_id: 任务ID，如果提供则优先使用，否则从context获取
        node_id: 节点ID，如果不提供则自动生成UUID
        parent_node_id: 父节点ID，用于建立节点层级关系
        msg_id: 消息ID，关联的消息ID
        msg_from: 消息发送者
        group_id: 组ID
        sub_group_root_id: 子组根节点ID
        metadata: 元数据字典
    
    Yields:
        node: 创建的RunNode对象，如果创建成功则返回，否则返回None
    
    Example:
        async with managed_runtime_node(
            context=message.context,
            busi_type=RunNodeBusiType.LLM,
            busi_id="",
            parent_node_id=message.id,
            msg_id=message.id
        ) as node:
            # 执行操作
            result = await some_operation()
            # 如果操作成功，上下文管理器会自动调用run_succeed
            # 如果发生异常，会自动调用run_failed
    """
    from aworld.runners.state_manager import RuntimeStateManager
    
    state_manager = RuntimeStateManager.instance()
    
    # 获取session_id和task_id，优先使用传入的参数，否则从context获取
    current_session_id = session_id
    current_task_id = task_id
    
    if context:
        if current_session_id is None and hasattr(context, 'session_id'):
            current_session_id = context.session_id
        if current_task_id is None and hasattr(context, 'task_id'):
            current_task_id = context.task_id
    
    # 创建节点
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
    
    # 如果节点创建成功，开始运行
    if node and hasattr(node, 'node_id'):
        state_manager.run_node(node.node_id)
    
    try:
        yield node
        # 如果执行成功，标记为成功
        if node and hasattr(node, 'node_id'):
            state_manager.run_succeed(node.node_id)
    except Exception:
        # 如果发生异常，标记为失败
        if node and hasattr(node, 'node_id'):
            state_manager.run_failed(node.node_id)
        raise

