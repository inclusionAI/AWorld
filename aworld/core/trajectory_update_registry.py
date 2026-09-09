"""Dependency-light lifecycle for asynchronous trajectory projection updates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


class TrajectoryRegistryState(str, Enum):
    OPEN = "open"
    SEALED = "sealed"
    DRAINED = "drained"


class TrajectoryRegistrySealedError(RuntimeError):
    """Raised when a producer attempts to register after the source was sealed."""


@dataclass(frozen=True, slots=True)
class TrajectoryUpdateOutcome:
    """Result returned by one build-and-store update."""

    build_succeeded: bool
    storage_acknowledged: bool
    persisted: bool = False
    superseded: bool = False
    item: Any = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.build_succeeded and self.storage_acknowledged


@dataclass(slots=True)
class TrajectoryUpdateEntry:
    sequence: int
    task_id: str
    logical_step_id: str
    revision: int
    task: asyncio.Task[TrajectoryUpdateOutcome]


@dataclass(frozen=True, slots=True)
class TrajectoryDrainResult:
    task_id: str
    high_watermark: int
    scheduled: int
    completed: int
    failed: int
    pending: int
    timed_out: bool
    late_registrations: int
    source_not_finalized: bool
    logical_step_ids: tuple[str, ...]


@dataclass(slots=True)
class _TaskScope:
    state: TrajectoryRegistryState = TrajectoryRegistryState.OPEN
    next_sequence: int = 1
    high_watermark: int | None = None
    entries: list[TrajectoryUpdateEntry] = field(default_factory=list)
    late_registrations: int = 0
    source_not_finalized: bool = False
    drain_result: TrajectoryDrainResult | None = None


class TrajectoryUpdateRegistry:
    """Root-context registry holding strong references to update tasks.

    Registration is synchronous: a monotonically increasing sequence is assigned
    and the task is retained before control returns to the producer.
    """

    def __init__(self) -> None:
        self._scopes: dict[str, _TaskScope] = {}

    def open(self, task_id: str) -> None:
        scope = self._scopes.get(task_id)
        if scope is None:
            self._scopes[task_id] = _TaskScope()
            return
        if scope.state is not TrajectoryRegistryState.OPEN:
            raise RuntimeError(f"trajectory registry for task {task_id} is already {scope.state.value}")

    def is_open(self, task_id: str) -> bool:
        scope = self._scopes.get(task_id)
        return scope is not None and scope.state is TrajectoryRegistryState.OPEN

    def mark_source_not_finalized(self, task_id: str) -> None:
        scope = self._scopes.setdefault(task_id, _TaskScope())
        scope.source_not_finalized = True

    def schedule(
        self,
        *,
        task_id: str,
        logical_step_id: str,
        revision: int,
        update_factory: Callable[[], Awaitable[TrajectoryUpdateOutcome]],
    ) -> TrajectoryUpdateEntry:
        scope = self._scopes.setdefault(task_id, _TaskScope())
        if scope.state is not TrajectoryRegistryState.OPEN:
            scope.late_registrations += 1
            scope.source_not_finalized = True
            raise TrajectoryRegistrySealedError(
                f"trajectory source for task {task_id} was sealed at {scope.high_watermark}"
            )

        sequence = scope.next_sequence
        scope.next_sequence += 1

        async def run_update() -> TrajectoryUpdateOutcome:
            try:
                outcome = await update_factory()
                if not isinstance(outcome, TrajectoryUpdateOutcome):
                    return TrajectoryUpdateOutcome(
                        build_succeeded=False,
                        storage_acknowledged=False,
                        error=f"invalid update outcome: {type(outcome).__name__}",
                    )
                return outcome
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return TrajectoryUpdateOutcome(
                    build_succeeded=False,
                    storage_acknowledged=False,
                    error=f"{type(exc).__name__}: {exc}",
                )

        task = asyncio.create_task(
            run_update(),
            name=f"trajectory-update:{task_id}:{sequence}:{logical_step_id}:r{revision}",
        )
        entry = TrajectoryUpdateEntry(
            sequence=sequence,
            task_id=task_id,
            logical_step_id=logical_step_id,
            revision=revision,
            task=task,
        )
        scope.entries.append(entry)
        return entry

    def seal(self, task_id: str) -> int:
        scope = self._scopes.setdefault(task_id, _TaskScope())
        if scope.state is TrajectoryRegistryState.OPEN:
            scope.high_watermark = scope.next_sequence - 1
            scope.state = TrajectoryRegistryState.SEALED
        return scope.high_watermark or 0

    async def drain(self, task_id: str, *, timeout: float) -> TrajectoryDrainResult:
        scope = self._scopes.setdefault(task_id, _TaskScope())
        high_watermark = self.seal(task_id)
        if scope.drain_result is not None:
            prior = scope.drain_result
            if (
                prior.late_registrations != scope.late_registrations
                or prior.source_not_finalized != scope.source_not_finalized
            ):
                scope.drain_result = TrajectoryDrainResult(
                    task_id=prior.task_id,
                    high_watermark=prior.high_watermark,
                    scheduled=prior.scheduled,
                    completed=prior.completed,
                    failed=prior.failed,
                    pending=prior.pending,
                    timed_out=prior.timed_out,
                    late_registrations=scope.late_registrations,
                    source_not_finalized=scope.source_not_finalized,
                    logical_step_ids=prior.logical_step_ids,
                )
            return scope.drain_result

        entries = [entry for entry in scope.entries if entry.sequence <= high_watermark]
        tasks = {entry.task for entry in entries}
        timed_out = False
        timed_out_tasks: set[asyncio.Task[TrajectoryUpdateOutcome]] = set()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(timeout, 0.0))
            timed_out_tasks = set(pending)
            timed_out = bool(timed_out_tasks)
            for task in timed_out_tasks:
                task.cancel()
            if timed_out_tasks:
                # Cancellation cleanup is bounded too. A later storage commit is
                # rejected by the dataset task fence installed before snapshot.
                await asyncio.wait(timed_out_tasks, timeout=min(max(timeout, 0.0), 0.1))

        completed = 0
        failed = 0
        pending_count = len(timed_out_tasks)
        for entry in entries:
            if entry.task in timed_out_tasks:
                continue
            if entry.task.cancelled():
                failed += 1
                continue
            try:
                outcome = entry.task.result()
            except BaseException:
                failed += 1
            else:
                if outcome.succeeded:
                    completed += 1
                else:
                    failed += 1

        result = TrajectoryDrainResult(
            task_id=task_id,
            high_watermark=high_watermark,
            scheduled=len(entries),
            completed=completed,
            failed=failed,
            pending=pending_count,
            timed_out=timed_out,
            late_registrations=scope.late_registrations,
            source_not_finalized=scope.source_not_finalized,
            logical_step_ids=tuple(dict.fromkeys(entry.logical_step_id for entry in entries)),
        )
        scope.drain_result = result
        scope.state = TrajectoryRegistryState.DRAINED
        return result

    def state(self, task_id: str) -> TrajectoryRegistryState | None:
        scope = self._scopes.get(task_id)
        return scope.state if scope is not None else None

    def diagnostics(self, task_id: str) -> tuple[int, bool]:
        scope = self._scopes.get(task_id)
        if scope is None:
            return 0, False
        return scope.late_registrations, scope.source_not_finalized

    def release(self, task_id: str) -> None:
        """Drop completed task references while retaining the drained tombstone."""
        scope = self._scopes.get(task_id)
        if scope is None:
            return
        if scope.state is not TrajectoryRegistryState.DRAINED:
            raise RuntimeError(f"cannot release trajectory registry for {task_id} before drain")
        scope.entries.clear()
