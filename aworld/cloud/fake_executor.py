"""Deterministic in-process cloud executor used by lifecycle tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any

from aworld.cloud.errors import CloudError, CloudErrorCode
from aworld.cloud.executor import (
    CloudExecutor,
    EventCallback,
    ExecutionResult,
    ExecutorEvent,
    ExecutorHandle,
    ExecutorInspection,
    ExecutorRequest,
    ExecutorStatus,
)
from aworld.cloud.models import ExecutorId, RunFile, RunId, utc_now


@dataclass(frozen=True)
class FakeEventSpec:
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class FakeExecutionPlan:
    """One deterministic start/wait outcome selected by run ID."""

    start_failure: bool = False
    exit_code: int = 0
    error_code: str | None = None
    error_message: str | None = None
    files: tuple[RunFile, ...] = ()
    events: tuple[FakeEventSpec, ...] = ()
    block_until_released: bool = False
    wait_for_cancellation: bool = False
    reattachable: bool = True


@dataclass
class _FakeExecution:
    request: ExecutorRequest
    handle: ExecutorHandle
    plan: FakeExecutionPlan
    cancel_event: asyncio.Event
    release_event: asyncio.Event
    started_event: asyncio.Event
    result: ExecutionResult | None = None
    events_emitted: bool = False
    active_counted: bool = True


class FakeCloudExecutor(CloudExecutor):
    """Controllable executor with no Docker, subprocess, or filesystem dependency."""

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._plans: dict[RunId, FakeExecutionPlan] = {}
        self._executions: dict[ExecutorId, _FakeExecution] = {}
        self._run_handles: dict[RunId, ExecutorHandle] = {}
        self._next_id = 1
        self._active = 0
        self.max_active = 0
        self.start_calls: list[RunId] = []
        self.cancel_calls: list[ExecutorId] = []

    def set_plan(self, run_id: RunId, plan: FakeExecutionPlan) -> None:
        self._plans[run_id] = plan

    async def start(self, request: ExecutorRequest) -> ExecutorHandle:
        self.start_calls.append(request.run.id)
        plan = self._plans.get(request.run.id, FakeExecutionPlan())
        if plan.start_failure:
            raise CloudError(
                CloudErrorCode.EXECUTOR_UNAVAILABLE,
                "fake executor was configured to fail during start",
            )
        handle = ExecutorHandle(ExecutorId(f"fake-executor-{self._next_id}"))
        self._next_id += 1
        execution = _FakeExecution(
            request=request,
            handle=handle,
            plan=plan,
            cancel_event=asyncio.Event(),
            release_event=asyncio.Event(),
            started_event=asyncio.Event(),
        )
        execution.started_event.set()
        self._executions[handle.executor_id] = execution
        self._run_handles[request.run.id] = handle
        self._active += 1
        self.max_active = max(self.max_active, self._active)
        return handle

    async def wait(
        self,
        handle: ExecutorHandle,
        *,
        on_event: EventCallback,
    ) -> ExecutionResult:
        execution = self._executions.get(handle.executor_id)
        if execution is None:
            raise CloudError(
                CloudErrorCode.EXECUTOR_UNAVAILABLE,
                "fake executor identity does not exist",
            )
        if execution.result is not None:
            return execution.result
        if not execution.events_emitted:
            execution.events_emitted = True
            for event in execution.plan.events:
                await on_event(
                    ExecutorEvent(
                        event_type=event.event_type,
                        payload=event.payload,
                        created_at=self._clock(),
                    )
                )
        try:
            if execution.plan.wait_for_cancellation:
                await execution.cancel_event.wait()
            elif execution.plan.block_until_released:
                cancel_wait = asyncio.create_task(execution.cancel_event.wait())
                release_wait = asyncio.create_task(execution.release_event.wait())
                gate_tasks = {cancel_wait, release_wait}
                try:
                    done, _ = await asyncio.wait(
                        gate_tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        task.result()
                finally:
                    for task in gate_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*gate_tasks, return_exceptions=True)
            if execution.cancel_event.is_set():
                result = ExecutionResult(
                    exit_code=130,
                    finished_at=self._clock(),
                    error_code="cancelled",
                    error_message="execution cancelled",
                )
            else:
                result = ExecutionResult(
                    exit_code=execution.plan.exit_code,
                    finished_at=self._clock(),
                    error_code=execution.plan.error_code,
                    error_message=execution.plan.error_message,
                    files=execution.plan.files,
                )
            execution.result = result
            if execution.active_counted:
                execution.active_counted = False
                self._active -= 1
            return result
        except asyncio.CancelledError:  # noqa: TRY203 - preserve running fake state
            raise

    async def inspect(self, executor_id: ExecutorId) -> ExecutorInspection:
        execution = self._executions.get(executor_id)
        if execution is None:
            return ExecutorInspection(status=ExecutorStatus.NOT_FOUND)
        if execution.result is not None:
            return ExecutorInspection(
                status=ExecutorStatus.EXITED,
                result=execution.result,
                reattachable=True,
            )
        return ExecutorInspection(
            status=ExecutorStatus.RUNNING,
            reattachable=execution.plan.reattachable,
        )

    async def cancel(
        self,
        executor_id: ExecutorId,
        *,
        grace_period: timedelta,
    ) -> None:
        del grace_period
        execution = self._executions.get(executor_id)
        if execution is None:
            return
        self.cancel_calls.append(executor_id)
        execution.cancel_event.set()

    async def wait_until_started(self, run_id: RunId) -> ExecutorHandle:
        while run_id not in self._run_handles:
            await asyncio.sleep(0)
        handle = self._run_handles[run_id]
        await self._executions[handle.executor_id].started_event.wait()
        return handle

    def release(self, run_id: RunId) -> None:
        handle = self._run_handles[run_id]
        self._executions[handle.executor_id].release_event.set()

    def handle_for(self, run_id: RunId) -> ExecutorHandle | None:
        return self._run_handles.get(run_id)
