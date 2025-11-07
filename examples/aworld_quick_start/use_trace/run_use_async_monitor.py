# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import time
import traceback

from aworld.logs.util import logger
from aworld.trace.asyncio_monitor.base import AsyncioMonitor


async def run():
    """
    >>> monitor = AsyncioMonitor(detect_duration_second=1, slow_task_ms=500)
    >>> monitor.start()
    >>> ...
    >>> monitor.stop()
    """
    monitor = AsyncioMonitor(detect_duration_second=1, slow_task_ms=500)
    monitor.start()

    try:
        async def short_task():
            await asyncio.sleep(0.1)
            return "Short task completed"

        async def slow_task():
            await asyncio.sleep(8)
            return "Slow task completed"

        async def waiting_task(wait_event):
            print("Waiting task started")
            await wait_event.wait()
            print("Waiting task resumed")
            return "Waiting task completed"

        async def concurrent_task(task_id):
            print("Concurrent task 3 is sleeping for 3 seconds")
            time.sleep(3)
            return f"Concurrent task {task_id} completed"

        wait_event = asyncio.Event()

        tasks = []
        short_future = asyncio.create_task(short_task())
        tasks.append(short_future)
        slow_future = asyncio.create_task(slow_task())
        tasks.append(slow_future)
        waiting_future = asyncio.create_task(waiting_task(wait_event))
        tasks.append(waiting_future)
        for i in range(1, 6):
            concurrent_future = asyncio.create_task(concurrent_task(i))
            tasks.append(concurrent_future)

        await asyncio.sleep(1.2)
        wait_event.set()

        await asyncio.gather(*tasks)
        await asyncio.sleep(2)
    except Exception as e:
        logger.error(traceback.format_exc())
    finally:
        monitor.stop()


if __name__ == "__main__":
    asyncio.run(run())
