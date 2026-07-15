from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Awaitable, Callable, Literal, Mapping

from aworld.core.task import Task, TaskResponse


BatchFailurePolicy = Literal["indexed_fail_fast", "collect_all"]
BatchResultStatus = Literal["succeeded", "failed", "cancelled", "discarded"]
RunTaskCallable = Callable[[Task], Awaitable[Mapping[str, TaskResponse]]]


@dataclass(frozen=True)
class TaskResourceClaim:
    key: str
    exclusive: bool = True

    def __post_init__(self) -> None:
        if not self.key or not self.key.strip():
            raise ValueError("resource claim key must not be empty")


@dataclass(frozen=True)
class TaskBatchItem:
    index: int
    task: Task
    resource_claims: tuple[TaskResourceClaim, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or self.index < 0:
            raise ValueError("task batch item index must be non-negative")


@dataclass(frozen=True)
class TaskBatchResult:
    index: int
    task_id: str
    status: BatchResultStatus
    response: TaskResponse | None = None
    error_type: str | None = None
    error_message: str | None = None
    queue_wait_seconds: float = 0.0
    execution_seconds: float = 0.0
    serialized_by_resource: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


class _ResourceCoordinator:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active: dict[str, list[bool]] = {}

    @staticmethod
    def _normalize(
        claims: tuple[TaskResourceClaim, ...],
    ) -> tuple[TaskResourceClaim, ...]:
        by_key: dict[str, bool] = {}
        for claim in claims:
            key = claim.key.strip()
            by_key[key] = by_key.get(key, False) or claim.exclusive
        return tuple(
            TaskResourceClaim(key=key, exclusive=exclusive)
            for key, exclusive in sorted(by_key.items())
        )

    def _conflicts(self, claims: tuple[TaskResourceClaim, ...]) -> bool:
        for claim in claims:
            active = self._active.get(claim.key, [])
            if active and (claim.exclusive or any(active)):
                return True
        return False

    async def acquire(
        self,
        claims: tuple[TaskResourceClaim, ...],
    ) -> tuple[tuple[TaskResourceClaim, ...], bool]:
        normalized = self._normalize(claims)
        waited = False
        async with self._condition:
            while self._conflicts(normalized):
                waited = True
                await self._condition.wait()
            for claim in normalized:
                self._active.setdefault(claim.key, []).append(claim.exclusive)
        return normalized, waited

    async def release(self, claims: tuple[TaskResourceClaim, ...]) -> None:
        if not claims:
            return
        async with self._condition:
            for claim in claims:
                active = self._active.get(claim.key)
                if not active:
                    continue
                active.remove(claim.exclusive)
                if not active:
                    self._active.pop(claim.key, None)
            self._condition.notify_all()


class DeterministicTaskBatchExecutor:
    """Run local AWorld Tasks concurrently and reduce results by stable index."""

    def __init__(self, *, run_task: RunTaskCallable | None = None) -> None:
        if run_task is None:
            from aworld.runner import Runners

            run_task = Runners.run_task
        self._run_task = run_task
        self.last_run_observability: dict[str, int | float | None] = {}

    async def run(
        self,
        items: list[TaskBatchItem],
        *,
        max_concurrency: int,
        failure_policy: BatchFailurePolicy,
    ) -> list[TaskBatchResult]:
        if isinstance(max_concurrency, bool) or max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        if failure_policy not in {"indexed_fail_fast", "collect_all"}:
            raise ValueError(f"unsupported failure policy: {failure_policy}")

        ordered = sorted(items, key=lambda item: item.index)
        indexes = [item.index for item in ordered]
        if len(indexes) != len(set(indexes)):
            raise ValueError("task batch item indexes must be unique")
        if not ordered:
            self.last_run_observability = {
                "configured_concurrency": max_concurrency,
                "effective_concurrency": 0,
                "max_observed_concurrency": 0,
                "failure_cutoff_index": None,
                "resource_serialized_count": 0,
                "elapsed_seconds": 0.0,
            }
            return []

        started_at = time.monotonic()
        semaphore = asyncio.Semaphore(min(max_concurrency, len(ordered)))
        resources = _ResourceCoordinator()
        results: dict[int, TaskBatchResult] = {}
        task_by_index: dict[int, asyncio.Task[TaskBatchResult]] = {}
        cutoff_lock = asyncio.Lock()
        failure_cutoff: int | None = None
        active_count = 0
        max_observed_concurrency = 0

        async def record_failure(index: int) -> None:
            nonlocal failure_cutoff
            if failure_policy != "indexed_fail_fast":
                return
            async with cutoff_lock:
                if failure_cutoff is not None and failure_cutoff <= index:
                    return
                failure_cutoff = index
                for other_index, running in task_by_index.items():
                    if other_index > index and not running.done():
                        running.cancel()

        async def run_item(item: TaskBatchItem) -> TaskBatchResult:
            nonlocal active_count, max_observed_concurrency
            queued_at = time.monotonic()
            acquired_claims: tuple[TaskResourceClaim, ...] = ()
            serialized_by_resource = False
            execution_started = 0.0
            try:
                async with semaphore:
                    if failure_cutoff is not None and item.index > failure_cutoff:
                        return TaskBatchResult(
                            index=item.index,
                            task_id=item.task.id,
                            status="discarded",
                            queue_wait_seconds=time.monotonic() - queued_at,
                        )
                    acquired_claims, serialized_by_resource = await resources.acquire(
                        item.resource_claims
                    )
                    if failure_cutoff is not None and item.index > failure_cutoff:
                        return TaskBatchResult(
                            index=item.index,
                            task_id=item.task.id,
                            status="discarded",
                            queue_wait_seconds=time.monotonic() - queued_at,
                            serialized_by_resource=serialized_by_resource,
                        )
                    active_count += 1
                    max_observed_concurrency = max(
                        max_observed_concurrency,
                        active_count,
                    )
                    execution_started = time.monotonic()
                    try:
                        response_map = await self._run_task(item.task)
                    finally:
                        active_count -= 1
                    response = response_map.get(item.task.id)
                    status: BatchResultStatus = (
                        "succeeded"
                        if response is not None and response.success
                        else "failed"
                    )
                    result = TaskBatchResult(
                        index=item.index,
                        task_id=item.task.id,
                        status=status,
                        response=response,
                        error_type=(None if response is not None else "MissingTaskResponse"),
                        error_message=(
                            None
                            if response is not None
                            else "Runners.run_task did not return the task response"
                        ),
                        queue_wait_seconds=execution_started - queued_at,
                        execution_seconds=time.monotonic() - execution_started,
                        serialized_by_resource=serialized_by_resource,
                    )
                    if status == "failed":
                        await record_failure(item.index)
                    return result
            except asyncio.CancelledError:
                return TaskBatchResult(
                    index=item.index,
                    task_id=item.task.id,
                    status=(
                        "discarded"
                        if failure_cutoff is not None and item.index > failure_cutoff
                        else "cancelled"
                    ),
                    error_type="CancelledError",
                    queue_wait_seconds=max(0.0, time.monotonic() - queued_at),
                    execution_seconds=(
                        max(0.0, time.monotonic() - execution_started)
                        if execution_started
                        else 0.0
                    ),
                    serialized_by_resource=serialized_by_resource,
                )
            except BaseException as exc:
                result = TaskBatchResult(
                    index=item.index,
                    task_id=item.task.id,
                    status="failed",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    queue_wait_seconds=(
                        execution_started - queued_at
                        if execution_started
                        else time.monotonic() - queued_at
                    ),
                    execution_seconds=(
                        time.monotonic() - execution_started
                        if execution_started
                        else 0.0
                    ),
                    serialized_by_resource=serialized_by_resource,
                )
                await record_failure(item.index)
                return result
            finally:
                await resources.release(acquired_claims)

        for item in ordered:
            task_by_index[item.index] = asyncio.create_task(run_item(item))

        gathered = await asyncio.gather(
            *(task_by_index[item.index] for item in ordered),
            return_exceptions=True,
        )
        for item, result in zip(ordered, gathered):
            if isinstance(result, BaseException):
                results[item.index] = TaskBatchResult(
                    index=item.index,
                    task_id=item.task.id,
                    status=(
                        "discarded"
                        if failure_cutoff is not None and item.index > failure_cutoff
                        else "cancelled"
                    ),
                    error_type=type(result).__name__,
                    error_message=str(result),
                )
            else:
                results[result.index] = result

        if failure_cutoff is not None:
            for item in ordered:
                if item.index <= failure_cutoff:
                    continue
                current = results[item.index]
                if current.status != "discarded":
                    results[item.index] = replace(
                        current,
                        status="discarded",
                        response=None,
                    )

        output = [results[item.index] for item in ordered]
        self.last_run_observability = {
            "configured_concurrency": max_concurrency,
            "effective_concurrency": min(max_concurrency, len(ordered)),
            "max_observed_concurrency": max_observed_concurrency,
            "failure_cutoff_index": failure_cutoff,
            "resource_serialized_count": sum(
                1 for result in output if result.serialized_by_resource
            ),
            "elapsed_seconds": time.monotonic() - started_at,
        }
        return output
