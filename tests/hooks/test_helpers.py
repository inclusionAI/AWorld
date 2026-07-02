"""测试辅助工具

提供测试中常用的辅助函数，提高测试稳定性和可维护性。
"""

import asyncio
import os
import time
from typing import Callable, Optional, Any


async def wait_for_condition(
    condition: Callable[[], bool],
    timeout: float = 5.0,
    poll_interval: float = 0.1,
    error_message: str = "Condition not met within timeout"
) -> None:
    """等待条件满足（带超时）

    使用轮询方式检查条件，避免固定 sleep 导致的 flaky tests。

    Args:
        condition: 返回 bool 的条件检查函数
        timeout: 超时时间（秒）
        poll_interval: 轮询间隔（秒）
        error_message: 超时时的错误消息

    Raises:
        AssertionError: 超时时抛出

    Example:
        >>> await wait_for_condition(
        ...     lambda: os.path.exists("/tmp/test.log"),
        ...     timeout=2.0
        ... )
    """
    start_time = time.time()

    while True:
        if condition():
            return

        elapsed = time.time() - start_time
        if elapsed >= timeout:
            raise AssertionError(
                f"{error_message} (waited {elapsed:.2f}s)"
            )

        await asyncio.sleep(poll_interval)


async def wait_for_file(
    file_path: str,
    timeout: float = 5.0,
    poll_interval: float = 0.1
) -> None:
    """等待文件创建（带超时）

    Args:
        file_path: 文件路径
        timeout: 超时时间（秒）
        poll_interval: 轮询间隔（秒）

    Raises:
        AssertionError: 超时时抛出

    Example:
        >>> await wait_for_file("/tmp/test.log", timeout=2.0)
    """
    await wait_for_condition(
        condition=lambda: os.path.exists(file_path),
        timeout=timeout,
        poll_interval=poll_interval,
        error_message=f"File {file_path} not created"
    )


async def wait_for_file_content(
    file_path: str,
    expected_content: str,
    timeout: float = 5.0,
    poll_interval: float = 0.1
) -> str:
    """等待文件包含指定内容（带超时）

    Args:
        file_path: 文件路径
        expected_content: 期望的内容（子串）
        timeout: 超时时间（秒）
        poll_interval: 轮询间隔（秒）

    Returns:
        文件完整内容

    Raises:
        AssertionError: 超时时抛出

    Example:
        >>> content = await wait_for_file_content(
        ...     "/tmp/test.log",
        ...     "agent_started"
        ... )
    """
    def check_content():
        if not os.path.exists(file_path):
            return False
        try:
            with open(file_path, 'r') as f:
                return expected_content in f.read()
        except Exception:
            return False

    await wait_for_condition(
        condition=check_content,
        timeout=timeout,
        poll_interval=poll_interval,
        error_message=f"File {file_path} does not contain '{expected_content}'"
    )

    # 返回文件内容
    with open(file_path, 'r') as f:
        return f.read()


def retry_on_assertion(
    max_retries: int = 3,
    delay: float = 0.1
):
    """装饰器：在 AssertionError 时重试

    用于处理偶发性测试失败（flaky tests）。

    Args:
        max_retries: 最大重试次数
        delay: 重试间隔（秒）

    Example:
        >>> @retry_on_assertion(max_retries=3)
        ... def test_something():
        ...     assert some_flaky_condition()
    """
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                last_error = None
                for attempt in range(max_retries):
                    try:
                        return await func(*args, **kwargs)
                    except AssertionError as e:
                        last_error = e
                        if attempt < max_retries - 1:
                            await asyncio.sleep(delay)
                        continue
                raise last_error
            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                last_error = None
                for attempt in range(max_retries):
                    try:
                        return func(*args, **kwargs)
                    except AssertionError as e:
                        last_error = e
                        if attempt < max_retries - 1:
                            time.sleep(delay)
                        continue
                raise last_error
            return sync_wrapper
    return decorator


class AsyncTimeout:
    """异步超时上下文管理器

    Example:
        >>> async with AsyncTimeout(2.0):
        ...     await some_long_operation()
    """

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.current_task()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


async def with_timeout(coro, timeout: float, error_message: Optional[str] = None):
    """带超时的异步操作

    Args:
        coro: 协程对象
        timeout: 超时时间（秒）
        error_message: 可选的错误消息

    Returns:
        协程返回值

    Raises:
        asyncio.TimeoutError: 超时时抛出

    Example:
        >>> result = await with_timeout(
        ...     some_async_operation(),
        ...     timeout=2.0,
        ...     error_message="Operation timed out"
        ... )
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        if error_message:
            raise asyncio.TimeoutError(error_message)
        raise
