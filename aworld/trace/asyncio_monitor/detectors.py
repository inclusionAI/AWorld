import asyncio
import time
from abc import ABC, abstractmethod
from typing import List, Dict, Any

from aworld.logs.util import asyncio_monitor_logger as logger
from aworld.trace.asyncio_monitor.util import get_task_stack_info, report_stack_info


class MonitorDetector(ABC):
    @abstractmethod
    def get_name(self) -> str:
        pass

    @abstractmethod
    def collect(self, tasks: List[asyncio.Task], monitor: 'AsyncioMonitor') -> Dict[str, Any]:
        pass

    @abstractmethod
    def report(self, data: Dict[str, Any], monitor: 'AsyncioMonitor', width: int) -> None:
        pass


class TaskCountDetector(MonitorDetector):
    def get_name(self) -> str:
        return "task_count"

    def collect(self, tasks: List[asyncio.Task], monitor: 'AsyncioMonitor') -> Dict[str, Any]:
        status_count = {'running': 0, 'pending': 0, 'done': 0, 'other': 0}

        for task in tasks:
            if task.done():
                status_count['done'] += 1
            elif hasattr(task, '_state'):
                if task._state.upper() == 'RUNNING':
                    status_count['running'] += 1
                elif task._state.upper() == 'PENDING':
                    status_count['pending'] += 1
                else:
                    status_count[task._state.lower()] += 1
            else:
                status_count['other'] = status_count.get('other', 0) + 1

        return {
            'total_tasks': len(tasks),
            'status_count': status_count
        }

    def report(self, data: Dict[str, Any], monitor: 'AsyncioMonitor', width: int) -> None:
        border_width = 3
        content_width = width - border_width
        first_col_ratio = 0.6

        first_col_width = int(content_width * first_col_ratio)
        second_col_width = content_width - first_col_width

        header_format = "| {:<%d} | {:<%d} |" % (first_col_width, second_col_width)
        separator_format = "|" + "-" * (first_col_width + 2) + "|" + "-" * (second_col_width + 2) + "|"

        logger.info(header_format.format("TASK STATISTICS", "VALUE"))
        logger.info(separator_format)

        logger.info(header_format.format("total tasks", str(data['total_tasks']).rjust(second_col_width)))
        for status, count in data['status_count'].items():
            logger.info(header_format.format(status + " tasks", str(count).rjust(second_col_width)))
        logger.info(separator_format)


class PendingReasonDetector(MonitorDetector):
    _pending_reasons = [
        ''
    ]

    def __init__(self) -> None:
        super().__init__()

    def get_name(self) -> str:
        return "pending_locations"

    def collect(self, tasks: List[asyncio.Task], monitor: 'AsyncioMonitor') -> Dict[str, Any]:
        pending_reasons_count = {}
        reason_tasks = {}

        for task in tasks:
            if task._state == 'PENDING':
                stack_info = get_task_stack_info(task)
                if stack_info:
                    for reason in self._pending_reasons:
                        for line in stack_info:
                            if reason in line:
                                pending_reasons_count[line] = pending_reasons_count.get(line, 0) + 1
                                reason_tasks[line] = task
                                break

        top_pengding_locations = sorted(pending_reasons_count.items(), key=lambda x: x[1], reverse=True)[:monitor.hot_location_top_n]

        top_location_task_stacks = {}

        for location, _ in top_pengding_locations:
            task = reason_tasks[location]
            top_location_task_stacks[location] = get_task_stack_info(task)

        return {
            'top_location_task_stacks': top_location_task_stacks,
            'top_pengding_locations': top_pengding_locations
        }

    def report(self, data: Dict[str, Any], monitor: 'AsyncioMonitor', width: int) -> None:
        border_width = 3
        content_width = width - border_width
        loc_col_ratio = 0.85
        count_col_ratio = 0.15

        loc_col_width = int(content_width * loc_col_ratio)
        count_col_width = content_width - loc_col_width

        loc_header_format = "| {:<%d} | {:<%d} |" % (loc_col_width, count_col_width)
        loc_separator_format = "|" + "-" * (loc_col_width + 2) + "|" + "-" * (count_col_width + 2) + "|"

        # print Top N pending locations
        logger.info("TOP {} PENDING LOCATIONS:".format(monitor.hot_location_top_n).center(width))
        logger.info("=" * (width + border_width))

        logger.info(loc_header_format.format("Pending location", "Waiters"))
        logger.info(loc_separator_format)

        if data['top_pengding_locations']:
            for location, count in data['top_pengding_locations']:
                max_loc_length = loc_col_width
                display_location = location[:max_loc_length - 3] + "..." if len(location) > max_loc_length else location
                logger.info(loc_header_format.format(display_location, str(count).rjust(count_col_width)))

            logger.info("=" * (width + border_width))
            # print stack trace for each pending location
            report_stack_info(data['top_location_task_stacks'])
        else:
            logger.info(loc_header_format.format("No hot pending locations", "-"))
            logger.info(loc_separator_format)


class BlockingLocationDetector(MonitorDetector):
    _blocking_functions = ['sleep', 'select', 'poll', 'accept', 'connect']
    _blocking_modules = ['time', 'socket', 'threading', 'queue']

    def __init__(self) -> None:
        super().__init__()

    def get_name(self) -> str:
        return "blocking_locations"

    def collect(self, tasks: List[asyncio.Task], monitor: 'AsyncioMonitor') -> Dict[str, Any]:
        blocking_locations = {}
        location_tasks = {}
        for task in tasks:
            if not task.done() and hasattr(task, '_started_at'):
                run_time = time.perf_counter() - task._started_at
                if run_time * 1000 < monitor.slow_task_ms:
                    continue

                stack_info = get_task_stack_info(task)
                if stack_info:
                    current_location = stack_info[-1] if stack_info else None
                    if current_location:
                        if any(func in current_location for func in self._blocking_functions) or any(module in current_location for module in self._blocking_modules):
                            if 'await' not in current_location:
                                blocking_locations[current_location] = blocking_locations.get(current_location, 0) + 1
                                location_tasks[current_location] = task

        top_blocking_locations = sorted(blocking_locations.items(), key=lambda x: x[1], reverse=True)[:monitor.hot_location_top_n]

        top_location_task_stacks = {}

        for location, _ in top_blocking_locations:
            task = location_tasks[location]
            top_location_task_stacks[location] = get_task_stack_info(task)

        return {
            'top_location_task_stacks': top_location_task_stacks,
            'top_blocking_locations': top_blocking_locations
        }

    def report(self, data: Dict[str, Any], monitor: 'AsyncioMonitor', width: int) -> None:
        border_width = 3
        content_width = width - border_width
        loc_col_ratio = 0.85

        loc_col_width = int(content_width * loc_col_ratio)
        count_col_width = content_width - loc_col_width

        loc_header_format = "| {:<%d} | {:<%d} |" % (loc_col_width, count_col_width)
        loc_separator_format = "|" + "-" * (loc_col_width + 2) + "|" + "-" * (count_col_width + 2) + "|"

        # print Top N blocking locations
        logger.info("TOP {} BLOCKING LOCATIONS:".format(monitor.hot_location_top_n).center(width))
        logger.info("=" * (width + border_width))

        logger.info(loc_header_format.format("Blocking location", "Waiters"))
        logger.info(loc_separator_format)

        if data['top_blocking_locations']:
            for location, count in data['top_blocking_locations']:
                max_loc_length = loc_col_width
                display_location = location[:max_loc_length - 3] + "..." if len(location) > max_loc_length else location
                logger.info(loc_header_format.format(display_location, str(count).rjust(count_col_width)))
            logger.info("=" * (width + border_width))
            # print stack trace for each blocking location
            report_stack_info(data['top_location_task_stacks'])
        else:
            logger.info(loc_header_format.format("No hot blocking locations", "-"))
            logger.info(loc_separator_format)
