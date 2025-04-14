# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import asyncio
import json
import threading
from typing import Callable, Any

from aworld.utils.import_package import import_package, import_packages


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
        finally:
            loop.close()
    return result
