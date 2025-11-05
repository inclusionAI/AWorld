import asyncio
import os
import time
import traceback
from typing import List, Dict, Any

from aworld.logs.util import asyncio_monitor_logger as logger


def get_task_running_location(task: asyncio.Task, shot_file_name: bool = False) -> str:
    coro = task.get_coro()
    if not coro:
        return None

    try:
        current_coro = coro
        last_valid_coro = current_coro
        visited_coros = set()
        while True:
            if current_coro in visited_coros:
                logger.debug("Detected coroutine cycle, breaking loop")
                break
            visited_coros.add(current_coro)
            if hasattr(current_coro, 'cr_await') and current_coro.cr_await:
                next_coro = current_coro.cr_await
                if hasattr(next_coro, 'cr_frame') and next_coro.cr_frame:
                    frame = next_coro.cr_frame
                    if frame.f_lasti >= 0:
                        # f_lasti >= 0 means the frame is active and not yet completed
                        current_coro = next_coro
                        last_valid_coro = current_coro
                    else:
                        break
                else:
                    break
            else:
                break

        if hasattr(last_valid_coro, 'cr_frame') and last_valid_coro.cr_frame:
            frame = last_valid_coro.cr_frame
            if shot_file_name:
                filename = os.path.basename(frame.f_code.co_filename)
            else:
                filename = frame.f_code.co_filename
            return f"{filename}:{frame.f_code.co_name}:{frame.f_lineno}"
    except Exception as frame_error:
        logger.error(f"Error accessing frame info: {frame_error}")
    return None


def get_task_stack_info(task: asyncio.Task) -> List[str]:
    stack_info = []
    try:
        frames = task.get_stack() if not task.done() else []
        if hasattr(task.get_coro(), 'cr_frame'):
            frames.append(task.get_coro().cr_frame)

        for frame in frames:
            stack = traceback.extract_stack(frame)
            for line in stack:
                stack_info.append(f"{line.filename}:{line.lineno}:{line.line.strip()}")
    except Exception as frame_error:
        logger.error(f"Error accessing frame info: {frame_error}")
    return stack_info
