"""Async entry-point policy for direct CLI runs.

Ordinary CLI invocations retain ``asyncio.run`` semantics.  One-shot runtimes
may opt into a bounded loop shutdown so a provider coroutine that ignores
cancellation cannot keep the task process alive after its outcome is known.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Coroutine
from typing import Any, TypeVar


BOUNDED_ASYNC_SHUTDOWN_ENV = "AWORLD_DIRECT_RUN_SHUTDOWN_TIMEOUT_SECONDS"
_MAX_SHUTDOWN_TIMEOUT_SECONDS = 30.0
_DEFAULT_ONE_SHOT_SHUTDOWN_TIMEOUT_SECONDS = 5.0
_T = TypeVar("_T")
logger = logging.getLogger(__name__)


def _bounded_shutdown_timeout() -> float | None:
    raw = os.environ.get(BOUNDED_ASYNC_SHUTDOWN_ENV)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{BOUNDED_ASYNC_SHUTDOWN_ENV} must be numeric") from exc
    if value <= 0 or value > _MAX_SHUTDOWN_TIMEOUT_SECONDS:
        raise ValueError(
            f"{BOUNDED_ASYNC_SHUTDOWN_ENV} must be greater than 0 and no more "
            f"than {_MAX_SHUTDOWN_TIMEOUT_SECONDS:g}"
        )
    return value


def _silence_destroyed_task_warning(task: asyncio.Task[Any]) -> None:
    # Bounded mode is used by a process-style one-shot runtime.  Once the
    # shutdown deadline expires, the OS is the final resource boundary.  The
    # warning would duplicate the explicit aggregate warning below.
    task._log_destroy_pending = False  # type: ignore[attr-defined]


def _shutdown_loop_with_deadline(
    loop: asyncio.AbstractEventLoop, *, timeout: float
) -> None:
    del timeout
    pending = {task for task in asyncio.all_tasks(loop) if not task.done()}
    # Never schedule provider-owned cancellation/async-generator code here.
    # Such code can synchronously block the event-loop thread before a timer
    # can fire.  In explicit one-shot mode the finalized outcome is followed
    # by a hard process exit, which is the only reliable boundary for unknown
    # Python tasks and non-daemon threads.
    if pending:
        for task in pending:
            _silence_destroyed_task_warning(task)
        logger.warning(
            "Direct-run one-shot shutdown is closing the event loop with %d "
            "provider-owned task(s) still pending",
            len(pending),
        )


def _run_with_bounded_shutdown(coro: Coroutine[Any, Any, _T], timeout: float) -> _T:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            try:
                _shutdown_loop_with_deadline(loop, timeout=timeout)
            except BaseException as cleanup_exc:
                # Cleanup must never replace the already established direct-run
                # result or exception.  A broken task can stop the loop while
                # handling cancellation; bounded one-shot mode will still use
                # the process boundary after outcome finalization.
                for task in asyncio.all_tasks(loop):
                    if not task.done():
                        _silence_destroyed_task_warning(task)
                logger.warning(
                    "Direct-run async cleanup failed; error_type=%s",
                    type(cleanup_exc).__name__,
                )
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def run_direct_async(
    coro: Coroutine[Any, Any, _T],
    *,
    one_shot: bool = False,
) -> _T:
    """Run direct-mode work with optional one-shot shutdown enforcement.

    ``aworld-cli run`` is a process-style one-shot command.  Its business
    coroutine may finish while provider or sandbox cleanup tasks remain
    blocked indefinitely.  In that mode the caller must be allowed to persist
    outcome/trajectory sidecars before using the process boundary.  Library
    callers retain normal ``asyncio.run`` behavior unless they opt in.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        coro.close()
        raise RuntimeError("run_direct_async() cannot be called from a running loop")
    try:
        timeout = _bounded_shutdown_timeout()
    except BaseException:
        coro.close()
        raise
    if timeout is None and one_shot:
        timeout = _DEFAULT_ONE_SHOT_SHUTDOWN_TIMEOUT_SECONDS
    if timeout is None:
        return asyncio.run(coro)
    return _run_with_bounded_shutdown(coro, timeout)


def hard_exit_direct_run_if_configured(
    exit_code: int,
    *,
    one_shot: bool = False,
) -> None:
    """Finish an opted-in one-shot process without waiting on stuck threads.

    This must be called only after outcome and trajectory finalization.  Python
    cannot forcibly stop an arbitrary non-daemon provider thread; ``os._exit``
    makes the process/container the final isolation boundary after flushing the
    user-visible streams.
    """

    if not one_shot and _bounded_shutdown_timeout() is None:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(exit_code)


__all__ = [
    "BOUNDED_ASYNC_SHUTDOWN_ENV",
    "hard_exit_direct_run_if_configured",
    "run_direct_async",
]
