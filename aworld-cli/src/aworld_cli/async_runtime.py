"""Async entry-point policy for direct CLI runs.

Ordinary CLI invocations retain ``asyncio.run`` semantics.  One-shot runtimes
may opt into a bounded loop shutdown so a provider coroutine that ignores
cancellation cannot keep the task process alive after its outcome is known.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

BOUNDED_ASYNC_SHUTDOWN_ENV = "AWORLD_DIRECT_RUN_SHUTDOWN_TIMEOUT_SECONDS"
FIRST_PROVIDER_START_TIMEOUT_ENV = (
    "AWORLD_DIRECT_RUN_FIRST_PROVIDER_TIMEOUT_SECONDS"
)
TASK_DEADLINE_EPOCH_ENV = "AWORLD_TASK_DEADLINE_EPOCH_SECONDS"
TASK_COMPLETION_RESERVE_ENV = "AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS"
_MAX_SHUTDOWN_TIMEOUT_SECONDS = 30.0
_EVIDENCE_POLL_INTERVAL_SECONDS = 0.05
_T = TypeVar("_T")
logger = logging.getLogger(__name__)


class DirectRunDeadlineExceeded(TimeoutError):
    """The caller-owned task deadline expired before direct mode returned."""

    def __init__(
        self,
        *,
        stage: str = "agent_execution",
        phase: str = "task_deadline",
    ) -> None:
        self.stage = stage
        self.phase = phase
        super().__init__(f"direct-run deadline exceeded during {phase}")


def _bounded_stack_label(value: object, *, fallback: str) -> str:
    text = value if isinstance(value, str) and value else fallback
    sanitized = "".join(
        character
        if character.isalnum() or character in {".", "_", "-"}
        else "_"
        for character in text
    )
    return sanitized[:128] or fallback


def _safe_task_stack(
    task: asyncio.Task[Any],
    *,
    limit: int = 8,
) -> list[dict[str, object]]:
    """Return a bounded control-plane stack without locals or source text."""

    try:
        frames = task.get_stack(limit=limit)
    except Exception:
        return []
    result: list[dict[str, object]] = []
    for frame in frames[-limit:]:
        code = frame.f_code
        result.append(
            {
                "module": _bounded_stack_label(
                    frame.f_globals.get("__name__"),
                    fallback="unknown",
                ),
                "file": _bounded_stack_label(
                    os.path.basename(code.co_filename),
                    fallback="unknown",
                ),
                "function": _bounded_stack_label(
                    code.co_name,
                    fallback="unknown",
                ),
                "line": max(0, int(frame.f_lineno)),
            }
        )
    return result


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


def _direct_run_timeout() -> float | None:
    """Return the time available before the caller's completion reserve.

    The process supervisor owns the absolute deadline.  Direct mode must stop
    early enough to replace its initial ``in_progress`` ATIF checkpoint with a
    terminal outcome and atomically persist the matching outcome sidecar.
    """

    raw_deadline = os.environ.get(TASK_DEADLINE_EPOCH_ENV)
    if raw_deadline is None or not raw_deadline.strip():
        return None
    raw_reserve = os.environ.get(TASK_COMPLETION_RESERVE_ENV, "0")
    try:
        deadline = float(raw_deadline)
        reserve = float(raw_reserve)
    except ValueError as exc:
        raise ValueError(
            f"{TASK_DEADLINE_EPOCH_ENV} and {TASK_COMPLETION_RESERVE_ENV} "
            "must be numeric"
        ) from exc
    if not math.isfinite(deadline) or deadline <= 0:
        raise ValueError(f"{TASK_DEADLINE_EPOCH_ENV} must be positive and finite")
    if not math.isfinite(reserve) or reserve < 0:
        raise ValueError(
            f"{TASK_COMPLETION_RESERVE_ENV} must be non-negative and finite"
        )
    return max(0.0, deadline - time.time() - reserve)


def _first_provider_start_timeout() -> float | None:
    """Return the bounded startup budget anchored to the caller deadline.

    AWorld must not invent a process lifetime for ordinary CLI users.  The
    first-provider watchdog is therefore opt-in and is valid only when the
    enclosing supervisor supplied its absolute task deadline.  The relative
    cap identifies a stalled startup early, while the caller deadline remains
    the authoritative upper bound and retains space for terminal persistence.
    """

    raw_timeout = os.environ.get(FIRST_PROVIDER_START_TIMEOUT_ENV)
    if raw_timeout is None or not raw_timeout.strip():
        return None
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise ValueError(
            f"{FIRST_PROVIDER_START_TIMEOUT_ENV} must be positive and finite"
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(
            f"{FIRST_PROVIDER_START_TIMEOUT_ENV} must be positive and finite"
        )
    caller_remaining = _direct_run_timeout()
    if caller_remaining is None:
        raise ValueError(
            f"{FIRST_PROVIDER_START_TIMEOUT_ENV} requires "
            f"{TASK_DEADLINE_EPOCH_ENV}"
        )
    return min(timeout, caller_remaining)


async def run_with_first_provider_start_watchdog(
    coro: Coroutine[Any, Any, _T],
    *,
    evidence_observed: Callable[[], bool],
) -> _T:
    """Bound pre-provider startup, then leave normal generation untouched.

    ``evidence_observed`` must become true only when the current task records
    an explicit provider invocation/attempt.  Pre-provider ``StepOutput`` and
    compiler-only request records do not qualify.  Once the provider boundary
    is reached this watchdog is permanently disarmed; existing generation
    deadlines remain responsible for provider latency and stream progress.

    Like the outer direct-run deadline, this helper deliberately does not
    cancel untrusted provider work on timeout.  The CLI first persists its
    terminal outcome, after which the configured one-shot process boundary
    reaps remaining work.  This is a cooperative event-loop boundary; code
    that blocks the loop synchronously is still bounded by the caller's
    external process supervisor.
    """

    try:
        timeout = _first_provider_start_timeout()
    except BaseException:
        coro.close()
        raise
    if timeout is None:
        return await coro

    task = asyncio.create_task(coro)
    expires_at = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            if evidence_observed():
                return await task
        except Exception as exc:
            # Evidence is observability, not execution authority.  A broken
            # probe must never abort a task that may otherwise complete.
            logger.warning(
                "Direct-run provider-evidence probe failed open; error_type=%s",
                type(exc).__name__,
            )
            return await task

        remaining = expires_at - asyncio.get_running_loop().time()
        if remaining <= 0:
            _silence_destroyed_task_warning(task)
            safe_stack = json.dumps(
                _safe_task_stack(task),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            logger.error(
                "Direct-run startup watchdog expired; "
                "stage=provider_start phase=awaiting_first_provider_attempt "
                "task_stack=%s",
                safe_stack,
            )
            raise DirectRunDeadlineExceeded(
                stage="provider_start",
                phase="awaiting_first_provider_attempt",
            )

        try:
            done, _pending = await asyncio.wait(
                {task},
                timeout=min(_EVIDENCE_POLL_INTERVAL_SECONDS, remaining),
            )
        except BaseException:
            if not task.done():
                _silence_destroyed_task_warning(task)
            raise
        if task in done:
            return task.result()


async def _run_until_deadline(
    coro: Coroutine[Any, Any, _T], *, timeout: float
) -> _T:
    task = asyncio.create_task(coro)
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        return task.result()
    # Do not cancel provider-owned work here.  Cancellation handlers are
    # outside our trust boundary and may block the loop synchronously.  The
    # explicit one-shot process boundary reaps them after final evidence is
    # persisted by the caller.
    _silence_destroyed_task_warning(task)
    raise DirectRunDeadlineExceeded()


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


def run_direct_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run direct-mode work with optional one-shot shutdown enforcement."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        coro.close()
        raise RuntimeError("run_direct_async() cannot be called from a running loop")
    try:
        shutdown_timeout = _bounded_shutdown_timeout()
        direct_timeout = _direct_run_timeout()
    except BaseException:
        coro.close()
        raise
    if direct_timeout is not None and shutdown_timeout is None:
        coro.close()
        raise ValueError(
            f"{TASK_DEADLINE_EPOCH_ENV} requires {BOUNDED_ASYNC_SHUTDOWN_ENV}"
        )
    bounded_coro = (
        _run_until_deadline(coro, timeout=direct_timeout)
        if direct_timeout is not None
        else coro
    )
    if shutdown_timeout is None:
        return asyncio.run(bounded_coro)
    return _run_with_bounded_shutdown(bounded_coro, shutdown_timeout)


def hard_exit_direct_run_if_configured(exit_code: int) -> None:
    """Finish an opted-in one-shot process without waiting on stuck threads.

    This must be called only after outcome and trajectory finalization.  Python
    cannot forcibly stop an arbitrary non-daemon provider thread; ``os._exit``
    makes the process/container the final isolation boundary after flushing the
    user-visible streams.
    """

    if _bounded_shutdown_timeout() is None:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(exit_code)


__all__ = [
    "BOUNDED_ASYNC_SHUTDOWN_ENV",
    "FIRST_PROVIDER_START_TIMEOUT_ENV",
    "TASK_COMPLETION_RESERVE_ENV",
    "TASK_DEADLINE_EPOCH_ENV",
    "DirectRunDeadlineExceeded",
    "hard_exit_direct_run_if_configured",
    "run_direct_async",
    "run_with_first_provider_start_watchdog",
]
