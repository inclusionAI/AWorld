import asyncio
import traceback
from typing import List
from types import FrameType

from aworld.logs.util import asyncio_monitor_logger as logger


def get_frames_of_coro_stack(task: asyncio.Task) -> list[FrameType]:
    coro = task.get_coro()
    if not coro:
        return None

    try:
        current_coro = coro
        last_valid_coro = current_coro
        visited_coros = set()
        coro_stack = []
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
                        coro_stack.append(current_coro)
                    else:
                        break
                else:
                    break
            else:
                break

        if coro is not last_valid_coro:
            result_frames = []
            for his_coro in coro_stack:
                if hasattr(his_coro, 'cr_frame') and his_coro.cr_frame:
                    result_frames.append(his_coro.cr_frame)
            return result_frames
    except Exception as frame_error:
        logger.error(f"Error accessing frame info: {frame_error}")
    return None


def get_task_stack_info(task: asyncio.Task) -> List[str]:
    stack_info = []
    try:
        frames = task.get_stack() if not task.done() else []
        if hasattr(task.get_coro(), 'cr_frame'):
            frames.append(task.get_coro().cr_frame)

        frames_of_coro_stack = get_frames_of_coro_stack(task)
        if frames_of_coro_stack:
            frames.extend(frames_of_coro_stack)
        for frame in frames:
            stack = traceback.extract_stack(frame)
            for line in stack:
                line_str = f"{line.filename}:{line.lineno}:{line.line.strip()}"
                if line_str not in stack_info:
                    stack_info.append(line_str)
    except Exception as frame_error:
        logger.error(f"Error accessing frame info: {frame_error}")
    return stack_info


def report_stack_info(location_stack_info: dict, max_frames: int = 5):
    for location, stack_info in location_stack_info.items():
        logger.info(f"  Stack trace for {location}:")
        for frame in stack_info[:max_frames]:
            logger.info(f"    {frame}")
        if len(stack_info) > max_frames:
            logger.info(f"    ... and {len(stack_info) - max_frames} more frames")
            logger.info(f"    {stack_info[-1]}")
