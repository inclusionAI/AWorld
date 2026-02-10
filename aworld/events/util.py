# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Callable, Any, List
import asyncio

from aworld.core.common import TaskStatusValue
from aworld.core.context.base import Context
from aworld.events import eventbus
from aworld.core.event.base import Message, Constants
from aworld.core.event.message_future import MessageFuture
from aworld.events.manager import EventManager
from aworld.utils.common import sync_exec


def subscribe(category: str, key: str = None):
    """Subscribe the special event to handle.

    Examples:
        >>> cate = Constants.TOOL or Constants.AGENT; key = "topic"
        >>> @subscribe(category=cate, key=key)
        >>> def example(message: Message) -> Message | None:
        >>>     print("do something")

    Args:
         category: Types of subscription events, the value is `agent` or `tool`, etc.
         key: The index key of the handler.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        topic = key
        if not topic:
            topic = category
        sync_exec(eventbus.subscribe, category, topic, func)
        return func

    return decorator


async def _send_message(msg: Message) -> str:
    context = msg.context
    if not context:
        context = Context()

    event_mng = context.event_manager
    if not event_mng:
        event_mng = EventManager(context)

    await event_mng.emit_message(msg)
    return msg.id


async def send_message(msg: Message):
    """Utility function of send event.

    Args:
        msg: The content and meta information to be sent.
    """
    context = msg.context
    if context:
        from aworld.core.common import TaskStatusValue
        task_status = await context.get_task_status()
        if task_status == TaskStatusValue.CANCELLED or task_status == TaskStatusValue.INTERRUPTED:
            await _send_finish_message(msg, task_status)
            return
    await _send_message(msg)


async def send_and_wait_message(msg: Message) -> List['HandleResult'] | None:
    """Send a message and wait for result (BLOCKING).
    
    WARNING: This blocks the calling thread until result is ready or timeout.
    For non-blocking result retrieval, use send_message_with_future() instead.

    Args:
        msg: Message to send

    Returns:
        List of HandleResult objects or None
    """
    context = msg.context
    if context:
        from aworld.core.common import TaskStatusValue
        task_status = await context.get_task_status()
        if task_status == TaskStatusValue.CANCELLED or task_status == TaskStatusValue.INTERRUPTED:
            await _send_finish_message(msg, task_status)
            return None
    await _send_message(msg)
    from aworld.runners.state_manager import RuntimeStateManager, RunNodeStatus, RunNodeBusiType
    state_mng = RuntimeStateManager.instance()
    msg_id = msg.id
    state_mng.create_node(
        node_id=msg_id,
        busi_type=RunNodeBusiType.from_message_category(msg.category),
        busi_id=msg.receiver or "",
        session_id=msg.session_id,
        task_id=msg.task_id,
        msg_id=msg_id,
        msg_from=msg.sender)
    res_node = await state_mng.wait_for_node_completion(msg_id)
    return res_node.results if res_node else None


async def send_message_with_future(msg: Message) -> MessageFuture:
    """Send message and return MessageFuture.
    
    Returns a Future object that can be awaited anywhere to get result.
    Sending the message returns immediately without blocking main thread.
    
    Args:
        msg: Message to send
    
    Returns:
        MessageFuture object that can be awaited
    
    Example:
        Basic usage:
            future = await send_message_with_future(msg)
            result = await future.wait(timeout=10)
        
        Sending multiple messages in parallel:
            futures = [
                await send_message_with_future(msg1),
                await send_message_with_future(msg2),
                await send_message_with_future(msg3),
            ]
            # Continue with other work...
            results = [await f.wait() for f in futures]
        
        Conditional waiting:
            future = await send_message_with_future(msg)
            if need_result_now:
                result = await future.wait(timeout=5)
            else:
                pass  # Don't need to wait
        
        Error handling:
            future = await send_message_with_future(msg)
            try:
                result = await future.wait(timeout=30)
                print(f"Success: {result.status}")
            except TimeoutError:
                print("Request timeout")
            except Exception as e:
                print(f"Error: {e}")
    """
    context = msg.context
    # Check if this is a MemoryEventMessage and if DIRECT mode is enabled
    from aworld.core.event.base import MemoryEventMessage
    from aworld.config.conf import HistoryWriteStrategy

    if isinstance(msg, MemoryEventMessage) and hasattr(msg, 'agent') and msg.agent:
        # Get history write strategy from agent's memory config
        write_strategy = HistoryWriteStrategy.EVENT_DRIVEN
        agent = msg.agent

        # Try to get from memory_config attribute first
        if hasattr(agent, 'memory_config') and hasattr(agent.memory_config, 'history_write_strategy'):
            write_strategy = agent.memory_config.history_write_strategy
        # Fallback to conf.memory_config
        elif hasattr(agent, 'conf') and hasattr(agent.conf, 'memory_config') and hasattr(agent.conf.memory_config, 'history_write_strategy'):
            write_strategy = agent.conf.memory_config.history_write_strategy

        # If direct call mode is enabled, call handler directly without going through message system
        if write_strategy == HistoryWriteStrategy.DIRECT:
            from aworld.runners.handler.memory import DefaultMemoryHandler
            from aworld.runners.state_manager import RunNode, RunNodeStatus
            context = context if hasattr(msg, 'context') and context else None
            if context:
                await DefaultMemoryHandler.handle_memory_message_directly(msg, context)
                # Return a completed future for DIRECT mode
                from aworld.logs.util import logger
                logger.debug(f"Handled memory message directly (DIRECT mode) for message {msg.id}")
                # Create a dummy future that's already completed with a success RunNode
                future = MessageFuture(msg.id)
                # Create a simple RunNode to represent successful direct handling
                success_node = RunNode(
                    node_id=msg.id,
                    status=RunNodeStatus.SUCCESS,
                    results=[],
                    msg_id=msg.id
                )
                # Mark as completed immediately
                future.future.set_result(success_node)
                return future


    if context:
        from aworld.core.common import TaskStatusValue
        task_status = await context.get_task_status()
        if task_status == TaskStatusValue.CANCELLED or task_status == TaskStatusValue.INTERRUPTED:
            await _send_finish_message(msg, task_status)
            # Task cancelled or interrupted, return a completed Future with empty result
            dummy_msg_id = f"cancelled_{msg.id}"
            future = MessageFuture(dummy_msg_id)
            future.set_empty_result(msg=f"Task {task_status.lower()}: message not sent")
            return future
    msg_id = await _send_message(msg)
    from aworld.logs.util import logger
    logger.debug(f"Created MessageFuture for message {msg_id}")
    future = MessageFuture(msg_id)
    return future

async def _send_finish_message(msg: Message, status: str = TaskStatusValue.SUCCESS):
    context = msg.context
    await _send_message(Message(payload=f"Task {status.lower()}",session_id=context.session_id, category=Constants.TASK, headers={"context": context}))


# ============================================================================
# Helper Functions: Manage Multiple Futures
# ============================================================================

async def gather_message_futures(
    *futures: MessageFuture,
    timeout: float = None,
    return_exceptions: bool = False
) -> List:
    """Wait for multiple MessageFutures in parallel (like asyncio.gather).
    
    Args:
        futures: Multiple MessageFuture objects
        timeout: Total timeout in seconds, None for infinite
        return_exceptions: Return exceptions instead of raising
    
    Returns:
        List of results
    
    Raises:
        TimeoutError: If wait times out
        Exception: If message failed (return_exceptions=False)
    
    Example:
        futures = [
            await send_message_with_future(msg1),
            await send_message_with_future(msg2),
            await send_message_with_future(msg3),
        ]
        results = await gather_message_futures(*futures, timeout=30)
        for result in results:
            print(f"Status: {result.status}")
    """
    tasks = [f.wait(timeout=timeout) for f in futures]
    return await asyncio.gather(*tasks, return_exceptions=return_exceptions)


async def wait_for_any(
    *futures: MessageFuture,
    timeout: float = None
) -> tuple:
    """Wait for any one MessageFuture to complete.
    
    Args:
        futures: Multiple MessageFuture objects
        timeout: Timeout in seconds, None for infinite
    
    Returns:
        (done, pending) tuple where:
        - done: Set of completed Futures
        - pending: Set of incomplete Futures
    
    Example:
        futures = [
            await send_message_with_future(msg1),
            await send_message_with_future(msg2),
            await send_message_with_future(msg3),
        ]
        done, pending = await wait_for_any(*futures, timeout=10)
        for f in done:
            result = f.result()
            print(f"Completed: {result}")
    """
    tasks = {asyncio.create_task(f.wait(timeout=timeout)): f for f in futures}
    done, pending = await asyncio.wait(
        tasks.keys(),
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED
    )
    return done, pending
