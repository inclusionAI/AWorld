# coding: utf-8
# Copyright (c) inclusionAI.
import asyncio
import contextvars
import inspect
import os
import threading
import time

from concurrent.futures import ThreadPoolExecutor
from typing import Coroutine, Generator, TypeVar, Any, List, Dict
from aworld.trace.asyncio_monitor.detectors import MonitorDetector, TaskCountDetector, PendingReasonDetector, \
    BlockingLocationDetector

from aworld.logs.util import asyncio_monitor_logger as logger

T_co = TypeVar("T_co", covariant=True)


class MonitoredTask(asyncio.Task):
    """A monitored task that records the start time and termination time."""

    def __init__(self, *args, slow_task_ms: int = 1000, **kwargs,):
        super().__init__(*args, **kwargs)
        self._started_at = time.perf_counter()
        self._last_check_running_time = self._started_at
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
                 check_interval: int = 5
                 ):
        self._monitored_loop = loop or asyncio.get_event_loop()
        self._monitored_loop.set_debug(True)
        self._monitored_loop.slow_callback_duration = 0.1
        self.hot_location_top_n = hot_location_top_n
        self.detect_duration_second = detect_duration_second
        self.shot_file_name = shot_file_name
        self.report_table_width = report_table_width
        self.slow_task_ms = slow_task_ms
        self.check_interval = check_interval

        self._pid = os.getpid()
        self._thread = None
        self._loop = None
        self._task = None
        self._stop_event = threading.Event()
        self._thread_executor = None
        self._detectors: Dict[str, MonitorDetector] = {}
        self._monitor_info: Dict[str, Dict[str, Any]] = {}
        self.add_detector([TaskCountDetector(), PendingReasonDetector(), BlockingLocationDetector()])

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    def add_detector(self, detectors: List[MonitorDetector]):
        for detector in detectors:
            self._detectors[detector.get_name()] = detector
            self._monitor_info[detector.get_name()] = {}

    def get_detector(self, name: str) -> MonitorDetector:
        return self._detectors.get(name)

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
        check_interval = self.check_interval
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
            self._monitor_info.clear()
            for name, detector in self._detectors.items():
                try:
                    self._monitor_info[name] = detector.collect(tasks, self)
                except Exception as e:
                    logger.error(f"Error in detector {name}: {e}")

            self._report_monitor_info(self.report_table_width)
        except Exception as e:
            logger.error(f"Error in analyze_and_report: {e}")

    def _report_monitor_info(self, total_width):

        border_width = 3
        total_width_with_border = total_width + border_width

        logger.info("=" * total_width_with_border)
        logger.info(f"ASYNCIO MONITOR INFO (PID: {self._pid})".center(total_width))
        logger.info("=" * total_width_with_border)

        for name, detector in self._detectors.items():
            try:
                if name in self._monitor_info and self._monitor_info[name]:
                    logger.info(f"[{name.upper()}]".center(total_width))
                    detector.report(self._monitor_info[name], self, total_width)
                    logger.info("=" * total_width_with_border)
            except Exception as e:
                logger.error(f"Error in reporting {name}: {e}")

    def _wrapped_create_task(self,
                             loop: asyncio.AbstractEventLoop,
                             coro: Coroutine[Any, Any, T_co] | Generator[Any, None, T_co],
                             *,
                             name: str | None = None,
                             context: contextvars.Context | None = None,) -> asyncio.Future[T_co]:
        assert loop is self._monitored_loop
        if context:
            return MonitoredTask(
                coro,  # type: ignore
                slow_task_ms=self.slow_task_ms,
                loop=self._monitored_loop,
                name=name,  # since Python 3.8
                context=context,  # since Python 3.11
            )
        else:
            return MonitoredTask(
                coro,  # type: ignore
                slow_task_ms=self.slow_task_ms,
                loop=self._monitored_loop,
                name=name,  # since Python 3.8
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
