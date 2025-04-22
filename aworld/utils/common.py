# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import re
import threading
from types import FunctionType
from typing import Callable, Any
import selectors


def convert_to_snake(name: str) -> str:
    """Class name convert to snake."""
    if '_' not in name:
        name = re.sub(r'([a-z])([A-Z])', r'\1_\2', name)
    return name.lower()


def is_abstract_method(cls, method_name):
    method = getattr(cls, method_name)
    return (hasattr(method, '__isabstractmethod__') and method.__isabstractmethod__) or (
            isinstance(method, FunctionType) and hasattr(
        method, '__abstractmethods__') and method in method.__abstractmethods__)


class ReturnThread(threading.Thread):
    def __init__(self, func, *args, **kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.result = None
        super().__init__()

    def run(self):
        self.result = asyncio.run(self.func(*self.args, **self.kwargs))


def asyncio_loop():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    return loop


def sync_exec(async_func: Callable[..., Any], *args, **kwargs):
    """Async function to sync execution."""
    if not asyncio.iscoroutinefunction(async_func):
        return async_func(*args, **kwargs)
    else:
        # result = asyncio.run(async_func(*args, **kwargs))  # 使用 asyncio.run 调用异步函数
        # loop = asyncio.new_event_loop()
        # selector = selectors.SelectSelector()
        # loop = asyncio.SelectorEventLoop(selector)
        # # asyncio.set_event_loop(loop)
        # try:
        #     result = loop.run_until_complete(async_func(*args, **kwargs))
        #     print('---------------getting result-----------')
        #     import pdb;pdb.set_trace()
        # except Exception as e:
        #     raise e
        # finally:
        #     loop.close()

        loop = asyncio_loop()
        if loop and loop.is_running():
            thread = ReturnThread(async_func, *args, **kwargs)
            thread.setDaemon(True)
            thread.start()
            thread.join()
            result = thread.result

        else:
            # loop = asyncio.new_event_loop()
            selector = selectors.SelectSelector()
            loop = asyncio.SelectorEventLoop(selector)
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(async_func(*args, **kwargs))
                print('---------------getting result-----------')
            except Exception as e:
                raise e
            # finally:
            #     loop.close()
    return result


def sync_exec_open(async_func: Callable[..., Any], *args, **kwargs):
    """Async function to sync execution."""
    if not asyncio.iscoroutinefunction(async_func):
        return async_func(*args, **kwargs)

    loop = asyncio_loop()
    if loop and loop.is_running():
        thread = ReturnThread(async_func, *args, **kwargs)
        thread.setDaemon(True)
        thread.start()
        thread.join()
        result = thread.result

    else:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(async_func(*args, **kwargs))
        except Exception as e:
            raise e
    return result