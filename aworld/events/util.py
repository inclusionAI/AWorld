# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Callable, Any, List
import asyncio

from aworld.core.context.base import Context
from aworld.core.event import eventbus
from aworld.core.event.base import Message, Constants
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


class MessageFuture:
    """Async message Future class, similar to JavaScript Promise.
    
    Core features:
    - Can await anywhere to get results
    - Returns immediately if result ready
    - Blocks at await point if result not ready until completion or timeout
    - Supports timeout handling
    - Supports try-catch error handling
    - Multiple locations can await same Future and get same result
    
    Example:
        # Send message, immediately returns Future
        future = await send_message_with_future(msg)
        
        # Main thread continues...
        await do_other_work()
        
        # Get result when needed
        try:
            result = await future.wait(timeout=10)
            print("Success:", result.status)
        except TimeoutError:
            print("Wait timeout")
    """
    
    def __init__(self, msg_id: str):
        """Initialize MessageFuture.
        
        Args:
            msg_id: Message ID to track
        """
        self.msg_id = msg_id
        from aworld.runners.state_manager import RuntimeStateManager
        self.state_mng = RuntimeStateManager.instance()
        
        # asyncio.Future() is the core - a waitable object
        # When set_result() is called, all await locations wake up
        self.future: asyncio.Future = asyncio.Future()
        
        # Record polling task for management
        self._task = None
        
        # Start background polling
        self._start_polling()
    
    def _start_polling(self):
        """Start background polling task."""
        self._task = asyncio.create_task(self._wait_internal())
    
    async def wait(self, timeout: float = None):
        """Wait for message completion and return result.
        
        Args:
            timeout: Timeout in seconds, None for infinite wait
        
        Returns:
            RunNode object containing:
            - node.status: RunNodeStatus execution status
            - node.results: List[HandleResult] results
            - node.result_msg: str result message
        
        Raises:
            TimeoutError: If wait times out
            Exception: If message execution failed or other error
        
        Example:
            try:
                result = await future.wait(timeout=30)
                print(f"Status: {result.status}")
                print(f"Results: {result.results}")
            except TimeoutError:
                print("Wait timeout")
        """
        try:
            if timeout is not None:
                return await asyncio.wait_for(self.future, timeout=timeout)
            else:
                return await self.future
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Waiting for message {self.msg_id} timed out after {timeout} seconds"
            )
        except asyncio.CancelledError:
            raise RuntimeError(f"Wait for message {self.msg_id} was cancelled")
    
    def done(self) -> bool:
        """Check if message completed.
        
        Returns:
            True if completed (success/failed), False if still processing
        """
        return self.future.done()
    
    def result(self):
        """Get result without waiting (raises if not completed).
        
        Returns:
            RunNode object if completed
        
        Raises:
            asyncio.InvalidStateError: If message not yet completed
        """
        return self.future.result()
    
    async def _wait_internal(self) -> None:
        """Background polling task.
        
        Workflow:
        1. Check message status every 100ms
        2. If completed, call set_result() on Future
        3. set_result() wakes up all await locations
        4. All await future.wait() calls receive result
        5. After 60 seconds, set timeout exception
        """
        from aworld.runners.state_manager import RuntimeStateManager
        from aworld.logs.util import logger
        
        max_retries = 600  # Max wait 60 seconds (600 * 0.1s)
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                node = self.state_mng.get_node(self.msg_id)
                
                # Node not yet created
                if node is None:
                    await asyncio.sleep(0.1)
                    retry_count += 1
                    continue
                
                # Node completed
                if node.has_finished():
                    logger.info(
                        f"Message {self.msg_id} finished with status: {node.status}"
                    )
                    
                    # Critical: set result
                    # This wakes up all await locations
                    if not self.future.done():
                        self.future.set_result(node)
                    return
                
                # Continue waiting
                await asyncio.sleep(0.1)
                retry_count += 1
                
            except Exception as e:
                logger.error(f"Error in _wait_internal for {self.msg_id}: {e}", exc_info=True)
                if not self.future.done():
                    self.future.set_exception(e)
                return
        
        # Timeout
        timeout_error = TimeoutError(
            f"Message {self.msg_id} did not complete within 60 seconds"
        )
        if not self.future.done():
            self.future.set_exception(timeout_error)


async def send_message_with_future(msg: Message) -> MessageFuture:
    """Send message and return MessageFuture.
    
    Returns a Future object that can be awaited anywhere to get result.
    Sending the message returns immediately without blocking main thread.
    
    Args:
        msg: Message to send
    
    Returns:
        MessageFuture object that can be awaited
    
    Example - Basic usage:
        future = await send_message_with_future(msg)
        result = await future.wait(timeout=10)
    
    Example - Parallel sending:
        futures = [
            await send_message_with_future(msg1),
            await send_message_with_future(msg2),
            await send_message_with_future(msg3),
        ]
        # Continue with other work...
        results = [await f.wait() for f in futures]
    
    Example - Conditional waiting:
        future = await send_message_with_future(msg)
        if need_result_now:
            result = await future.wait(timeout=5)
        else:
            pass  # Don't need to wait
    
    Example - Error handling:
        future = await send_message_with_future(msg)
        try:
            result = await future.wait(timeout=30)
            print(f"Success: {result.status}")
        except TimeoutError:
            print("Request timeout")
        except Exception as e:
            print(f"Error: {e}")
    """
    msg_id = await _send_message(msg)
    from aworld.logs.util import logger
    logger.debug(f"Created MessageFuture for message {msg_id}")
    future = MessageFuture(msg_id)
    return future


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
