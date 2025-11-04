import asyncio
import os
import threading
import contextvars
import time
import inspect

from concurrent.futures import ThreadPoolExecutor
from typing import Coroutine, Generator, TypeVar, Any


from aworld.logs.util import asyncio_monitor_logger as logger

T_co = TypeVar("T_co", covariant=True)


class MonitoredTask(asyncio.Task):
    '''
    A monitored task that records the start time and termination time.
    '''

    def __init__(self, *args, slow_task_ms: int = 1000, **kwargs,):
        super().__init__(*args, **kwargs)
        self._started_at = time.perf_counter()
        self._terminated_at = None
        self._slow_task_ms = slow_task_ms
        self._creation_location = self._get_create_location()
        self.add_done_callback(self._on_task_done)

    def _on_task_done(self, _: "asyncio.Task[Any]") -> None:
        self._terminated_at = time.perf_counter()
        if self._terminated_at - self._started_at > self._slow_task_ms / 1000:
            logger.warning(
                f"Slow task {self.get_name()}, duration: {self._terminated_at - self._started_at:.2f} seconds, created at {self._creation_location}")

    def _get_create_location(self):
        try:
            stack = inspect.stack()[3:]
            for frame_info in stack:
                if 'asyncio' not in frame_info.filename and 'asyncio_monitor.py' not in frame_info.filename:
                    filename = os.path.basename(frame_info.filename)
                    return f"{filename}:{frame_info.function}:{frame_info.lineno}"
            return "Unknown location"
        except Exception:
            return "Failed to get location"


class AsyncioMonitor:

    def __init__(self, loop=None,
                 hot_location_top_n: int = 5,
                 detect_duration_second: int = 5,
                 shot_file_name: bool = True,
                 report_table_width: int = 100,
                 slow_task_ms: int = 1000,
                 ):
        self._monitored_loop = loop or asyncio.get_event_loop()
        self._monitored_loop.set_debug(True)
        self._monitored_loop.slow_callback_duration = 0.1
        self.hot_location_top_n = hot_location_top_n
        self.detect_duration_second = detect_duration_second
        self.shot_file_name = shot_file_name
        self.report_table_width = report_table_width
        self.slow_task_ms = slow_task_ms

        self.pid = os.getpid()
        self._thread = None
        self._loop = None
        self._task = None
        self._stop_event = threading.Event()
        self._thread_executor = None

        self.monitor_info = {
            "task_count": 0,
            "status_count": {'running': 0, 'waiting': 0, 'done': 0},
            "waiting_reasons": {},
            "waiting_ratio": 0.0,
            "top_waiting_locations": [],
            "location_tasks": {},
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("Monitor is already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_monitor_thread, daemon=True)
        self._monitored_loop.set_task_factory(self._wrapped_create_task)
        self._thread.start()
        logger.info(f"AsyncioMonitor started in thread {self._thread.name}")

    def stop(self):
        if not self._thread or not self._thread.is_alive():
            logger.warning("Monitor is not running")
            return

        self._stop_event.set()
        if self._task and hasattr(self._loop, 'call_soon_threadsafe'):
            try:
                self._loop.call_soon_threadsafe(self._task.cancel)
            except Exception as e:
                logger.warning(f"Error in cancel monitor task: {e}")

        if self._thread:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning("Monitor thread did not terminate gracefully, it may be still running")
        if self._thread_executor:
            self._thread_executor.shutdown(wait=False)
            self._thread_executor = None

        self._task = None
        self._loop = None
        logger.info("AsyncioMonitor stopped")

    def _run_monitor_thread(self):
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._thread_executor = ThreadPoolExecutor(max_workers=1)
            self._task = self._loop.create_task(self._monitor_task_func())
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            logger.info("Monitor task cancelled")
        except Exception as e:
            logger.error(f"Error in monitor thread: {e}")
        finally:
            self._loop.close()
            if self._task and not self._task.done():
                self._task.cancel()
            if self._thread_executor:
                self._thread_executor.shutdown(wait=False)
            if self._loop:
                try:
                    for task in asyncio.all_tasks(loop=self._loop):
                        task.cancel()
                    self._loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop=self._loop), return_exceptions=True))
                except:
                    pass
                self._loop.close()

    async def _monitor_task_func(self):
        check_interval = 0.1
        report_interval = self.detect_duration_second
        last_report_time = time.time()

        while not self._stop_event.is_set():
            try:
                current_time = time.time()
                if current_time - last_report_time >= report_interval:
                    tasks = await self._loop.run_in_executor(
                        self._thread_executor,
                        lambda: asyncio.all_tasks(loop=self._monitored_loop)
                    )
                    self._analyze_and_report(tasks)
                    last_report_time = current_time
                await asyncio.sleep(check_interval)
            except asyncio.CancelledError:
                logger.debug("Monitor task received cancellation")
                break
            except Exception as e:
                logger.error(f"Error in monitor task: {e}")
                await asyncio.sleep(check_interval)

    def _analyze_and_report(self, tasks):
        try:
            self.monitor_info['task_count'] = len([t for t in tasks if not t.done()])
            status_count, waiting_reasons, location_tasks = self._task_analyze(tasks)
            self.monitor_info['status_count'] = status_count
            self.monitor_info['waiting_reasons'] = waiting_reasons
            self.monitor_info['location_tasks'] = location_tasks

            pending_ratio, top_waiting_locations = self._detect_deadlock(status_count, waiting_reasons)
            self.monitor_info['pending_ratio'] = pending_ratio
            self.monitor_info['top_waiting_locations'] = top_waiting_locations
            self._report_monitor_info(self.report_table_width)
        except Exception as e:
            logger.error(f"Error in analyze_and_report: {e}")

    def _task_analyze(self, tasks):
        status_count = {'running': 0, 'pending': 0, 'done': 0}
        waiting_reasons = {}
        location_tasks = {}

        try:
            for task in tasks:
                try:
                    if task.done():
                        status_count['done'] += 1
                        continue

                    if hasattr(task, '_state'):
                        if task._state == 'RUNNING':
                            status_count['running'] += 1
                        elif task._state == 'PENDING':
                            status_count['pending'] += 1
                            waiting_location = self._get_task_waiting_location(task)
                            if waiting_location:
                                waiting_reasons[waiting_location] = waiting_reasons.get(waiting_location, 0) + 1
                                if waiting_location not in location_tasks:
                                    location_tasks[waiting_location] = task
                    else:
                        coro = task.get_coro()
                        if coro and hasattr(coro, 'cr_frame') and coro.cr_frame:
                            waiting_location = self._get_task_waiting_location(task)
                            if waiting_location:
                                waiting_reasons[waiting_location] = waiting_reasons.get(waiting_location, 0) + 1
                                if waiting_location not in location_tasks:
                                    location_tasks[waiting_location] = task
                            status_count['waiting'] += 1
                        else:
                            status_count['running'] += 1
                except Exception as task_error:
                    logger.error(f"Error analyzing task: {task_error}")
                    continue
        except Exception as e:
            logger.error(f"Error in task_analyze: {e}")
        return status_count, waiting_reasons, location_tasks

    def _get_task_waiting_location(self, task):
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
                if self.shot_file_name:
                    filename = os.path.basename(frame.f_code.co_filename)
                else:
                    filename = frame.f_code.co_filename
                return f"{filename}:{frame.f_code.co_name}:{frame.f_lineno}"
        except Exception as frame_error:
            logger.error(f"Error accessing frame info: {frame_error}")
        return None

    def _detect_deadlock(self, status_count, waiting_reasons):

        pending_ratio = status_count['pending'] / max(1, sum(status_count.values()))
        if pending_ratio > 0.5:
            logger.warning(f"Detected pending ratio: {pending_ratio:.2f}")

        top_waiting_locations = sorted(waiting_reasons.items(), key=lambda x: x[1], reverse=True)[:self.hot_location_top_n]
        max_waiters = top_waiting_locations[0][1] if top_waiting_locations else 0
        if max_waiters > 5:
            hot_location = top_waiting_locations[0]
            logger.warning(f"Detected hot location: {hot_location[0]} with {hot_location[1]} waiters")

        return pending_ratio, top_waiting_locations

    def _report_monitor_info(self, total_width):

        border_width = 3
        first_col_ratio = 0.6
        second_col_ratio = 0.4
        total_width_with_border = total_width + border_width

        content_width = total_width - border_width
        first_col_width = int(content_width * first_col_ratio)
        second_col_width = content_width - first_col_width

        logger.info("=" * total_width_with_border)
        logger.info(f"ASYNCIO MONITOR INFO (PID: {self.pid})".center(total_width))
        logger.info("=" * total_width_with_border)

        # basic task statistics table (with border)
        header_format = "| {:<%d} | {:<%d} |" % (first_col_width, second_col_width)
        separator_format = "|" + "-" * (first_col_width + 2) + "|" + "-" * (second_col_width + 2) + "|"

        logger.info(header_format.format("TASK STATISTICS", "VALUE"))
        logger.info(separator_format)

        logger.info(header_format.format("Total tasks", str(self.monitor_info['task_count']).rjust(second_col_width)))
        logger.info(header_format.format("Running tasks", str(self.monitor_info['status_count']['running']).rjust(second_col_width)))
        logger.info(header_format.format("Pending tasks", str(self.monitor_info['status_count']['pending']).rjust(second_col_width)))
        logger.info(header_format.format("Done tasks", str(self.monitor_info['status_count']['done']).rjust(second_col_width)))
        logger.info(header_format.format("Pending ratio", f"{self.monitor_info['pending_ratio']:.2f}".rjust(second_col_width)))
        logger.info(separator_format)

        # top waiting locations table (with border)
        loc_col_ratio = 0.85
        count_col_ratio = 0.15
        loc_col_width = int(content_width * loc_col_ratio)
        count_col_width = content_width - loc_col_width
        loc_header_format = "| {:<%d} | {:<%d} |" % (loc_col_width, count_col_width)
        loc_separator_format = "|" + "-" * (loc_col_width + 2) + "|" + "-" * (count_col_width + 2) + "|"

        logger.info("TOP {} WAITING LOCATIONS:".format(self.hot_location_top_n).center(total_width))
        logger.info("=" * total_width_with_border)

        logger.info(loc_header_format.format("Waiting location", "Waiters"))
        logger.info(loc_separator_format)

        if self.monitor_info['top_waiting_locations']:
            for location, count in self.monitor_info['top_waiting_locations']:
                max_loc_length = loc_col_width
                display_location = location[:max_loc_length - 3] + "..." if len(location) > max_loc_length else location
                logger.info(loc_header_format.format(display_location, str(count).rjust(count_col_width)))
        else:
            logger.info(loc_header_format.format("No hot waiting locations", "-"))
            logger.info(loc_separator_format)
        logger.info("=" * total_width_with_border + "\n")

        if 'location_tasks' in self.monitor_info:
            for location, task in self.monitor_info['location_tasks'].items():
                stack_frames = self._get_task_full_stack(task)
                if stack_frames:
                    logger.info(f"{location} stack trace:")
                    for frame in stack_frames:
                        logger.info(frame)

    def _wrapped_create_task(self,
                             loop: asyncio.AbstractEventLoop,
                             coro: Coroutine[Any, Any, T_co] | Generator[Any, None, T_co],
                             *,
                             name: str | None = None,
                             context: contextvars.Context | None = None,) -> asyncio.Future[T_co]:
        assert loop is self._monitored_loop
        return MonitoredTask(
            coro,  # type: ignore
            slow_task_ms=self.slow_task_ms,
            loop=self._monitored_loop,
            name=name,  # since Python 3.8
            context=context,  # since Python 3.11
        )

    def _get_task_full_stack(self, task):
        coro = task.get_coro()
        if not coro:
            return []

        stack_frames = []
        visited_coros = set()

        try:
            current_coro = coro
            while True:
                if current_coro in visited_coros:
                    logger.debug("Detected coroutine cycle in stack extraction, breaking loop")
                    break
                visited_coros.add(current_coro)

                if hasattr(current_coro, 'cr_frame') and current_coro.cr_frame:
                    frame = current_coro.cr_frame
                    if self.shot_file_name:
                        filename = os.path.basename(frame.f_code.co_filename)
                    else:
                        filename = frame.f_code.co_filename
                    stack_frames.append(f"  at {filename}:{frame.f_code.co_name}:{frame.f_lineno}")

                if hasattr(current_coro, 'cr_await') and current_coro.cr_await:
                    current_coro = current_coro.cr_await
                else:
                    break
        except Exception as e:
            logger.error(f"Error getting full stack: {e}")

        return stack_frames
