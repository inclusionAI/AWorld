# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import asyncio
import contextlib
from collections.abc import Generator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed


@contextlib.contextmanager
def loop_in_new_thread() -> Generator[asyncio.AbstractEventLoop]:
    loop_future = Future[asyncio.AbstractEventLoop]()
    stop_event = asyncio.Event()

    async def create():
        loop_future.set_result(asyncio.get_running_loop())
        await stop_event.wait()

    with ThreadPoolExecutor(1) as pool:
        complete_future = pool.submit(asyncio.run, create())
        for future in as_completed((loop_future, complete_future)):
            if future is loop_future:
                loop = loop_future.result()
                try:
                    yield loop
                finally:
                    loop.call_soon_threadsafe(stop_event.set, )
            else:
                future.result()


def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def shutdown_loop(loop):
    loop.stop()


def shutdown_all(loops):
    [loop.call_soon_threadsafe(shutdown_loop, loop) for loop in loops]
