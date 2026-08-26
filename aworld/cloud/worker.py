"""Durable AWorld Cloud claim, execute, heartbeat, and reconcile worker."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

from aworld.cloud.errors import CloudError, CloudErrorCode
from aworld.cloud.executor import (
    CloudExecutor,
    ExecutionResult,
    ExecutorEvent,
    ExecutorHandle,
    ExecutorRequest,
    ExecutorStatus,
)
from aworld.cloud.models import (
    ExecutorId,
    Run,
    RunFileKind,
    RunId,
    RunMode,
    RunState,
    TrajectoryFormat,
    TrajectoryRole,
    WorkspaceId,
    WorkspaceState,
    transition_run,
    transition_workspace,
    utc_now,
)
from aworld.cloud.paths import CloudPaths
from aworld.cloud.repository import CloudRepository
from aworld.cloud.settings import CloudSettings

Clock = Callable[[], datetime]
_CANCELLATION_GRACE_PERIOD = timedelta(seconds=5)


class _RunOwnershipLost(Exception):
    """Internal signal that another durable worker adopted the run."""


class CloudWorker:
    """One durable worker instance with bounded in-process execution capacity."""

    def __init__(
        self,
        repository: CloudRepository,
        executor: CloudExecutor,
        settings: CloudSettings,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._settings = settings
        self._clock = clock
        self._paths = CloudPaths(settings)
        self._active: dict[RunId, asyncio.Task[None]] = {}
        self._stopping = asyncio.Event()

    @property
    def active_count(self) -> int:
        return sum(not task.done() for task in self._active.values())

    async def _append_event(
        self,
        run_id: RunId,
        event_type: str,
        payload: dict[str, object],
        *,
        created_at: datetime | None = None,
    ) -> None:
        await self._repository.append_event(
            run_id,
            event_type=event_type,
            payload=payload,
            created_at=created_at or self._clock(),
        )

    async def _reap(self) -> None:
        completed = [run_id for run_id, task in self._active.items() if task.done()]
        for run_id in completed:
            task = self._active.pop(run_id)
            await asyncio.gather(task, return_exceptions=True)

    def _schedule(self, run: Run, *, reattached: bool = False) -> None:
        if run.id in self._active:
            return
        if reattached:
            task = asyncio.create_task(self._resume_run(run))
        else:
            task = asyncio.create_task(self._execute_claimed_run(run))
        self._active[run.id] = task

    async def run_once(self) -> int:
        """Fill available capacity with durable queued work and return claims made."""

        await self._reap()
        claimed_count = 0
        while self.active_count < self._settings.concurrency:
            claimed = await self._repository.claim_run(
                worker_id=self._settings.worker_id,
                lease_expires_at=self._clock() + self._settings.lease_duration,
            )
            if claimed is None:
                break
            self._schedule(claimed)
            claimed_count += 1
        return claimed_count

    async def wait_for_idle(self) -> None:
        while self._active:
            tasks = tuple(self._active.values())
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._reap()

    async def run_until_idle(self, *, max_rounds: int = 100) -> None:
        for _ in range(max_rounds):
            claimed = await self.run_once()
            if self._active:
                await self.wait_for_idle()
                continue
            if claimed == 0:
                return
        raise RuntimeError("cloud worker did not become idle")

    async def run_forever(self) -> None:
        self._stopping.clear()
        await self.reconcile_startup()
        while not self._stopping.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self._settings.poll_interval.total_seconds(),
                )
            except asyncio.TimeoutError:
                pass
        await self.wait_for_idle()

    async def stop(self, *, graceful: bool = True) -> None:
        self._stopping.set()
        if graceful:
            await self.wait_for_idle()
            return
        tasks = tuple(self._active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._active.clear()

    async def _set_workspace_state(
        self,
        workspace_id: WorkspaceId,
        target: WorkspaceState,
    ) -> None:
        for _ in range(5):
            workspace = await self._repository.get_workspace(workspace_id)
            if workspace is None:
                raise CloudError(
                    CloudErrorCode.WORKSPACE_NOT_FOUND,
                    "workspace does not exist",
                )
            if workspace.state is target:
                return
            if (
                target is WorkspaceState.BUSY
                and workspace.state is not WorkspaceState.READY
            ):
                raise CloudError(
                    CloudErrorCode.INVALID_TRANSITION,
                    "workspace cannot become busy",
                )
            if (
                target is WorkspaceState.READY
                and workspace.state is not WorkspaceState.BUSY
            ):
                return
            candidate = transition_workspace(workspace, target, at=self._clock())
            try:
                await self._repository.update_workspace(
                    candidate,
                    expected_revision=workspace.revision,
                    expected_state=workspace.state,
                )
                return
            except CloudError as exc:
                if exc.code is not CloudErrorCode.REVISION_CONFLICT:
                    raise
        raise CloudError(
            CloudErrorCode.REVISION_CONFLICT,
            "workspace changed repeatedly during worker update",
        )

    async def _get_run(self, run_id: RunId) -> Run:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise CloudError(CloudErrorCode.RUN_NOT_FOUND, "run does not exist")
        return run

    async def _persist_executor_and_enter_running(
        self,
        run_id: RunId,
        executor_id: ExecutorId,
    ) -> Run:
        for _ in range(10):
            current = await self._get_run(run_id)
            if current.state not in {RunState.STARTING, RunState.CANCELLING}:
                return current
            if current.worker_id != self._settings.worker_id:
                raise _RunOwnershipLost
            if current.executor_id != executor_id:
                with_executor = replace(
                    current,
                    revision=current.revision + 1,
                    executor_id=executor_id,
                )
                try:
                    current = await self._repository.update_run(
                        with_executor,
                        expected_revision=current.revision,
                        expected_state=current.state,
                    )
                except CloudError as exc:
                    if exc.code is CloudErrorCode.REVISION_CONFLICT:
                        continue
                    raise
            if current.state is RunState.CANCELLING:
                return current
            running = transition_run(current, RunState.RUNNING, at=self._clock())
            try:
                return await self._repository.update_run(
                    running,
                    expected_revision=current.revision,
                    expected_state=RunState.STARTING,
                )
            except CloudError as exc:
                if exc.code is CloudErrorCode.REVISION_CONFLICT:
                    continue
                raise
        raise CloudError(
            CloudErrorCode.REVISION_CONFLICT,
            "run changed repeatedly while persisting executor identity",
        )

    async def _execute_claimed_run(self, claimed: Run) -> None:
        try:
            workspace = await self._repository.get_workspace(claimed.workspace_id)
            if workspace is None:
                raise CloudError(
                    CloudErrorCode.WORKSPACE_NOT_FOUND,
                    "workspace does not exist",
                )
            await self._set_workspace_state(workspace.id, WorkspaceState.BUSY)
            await self._append_event(
                claimed.id,
                "run.starting",
                {
                    "state": RunState.STARTING.value,
                    "worker_id": self._settings.worker_id,
                },
            )
            latest = await self._get_run(claimed.id)
            if latest.state is RunState.CANCELLING:
                await self._finalize_cancelled(latest.id)
                return
            profile = self._settings.profile(workspace.profile_name)
            output_directory = self._paths.provision_run_output(claimed.id)
            request = ExecutorRequest(
                workspace=workspace,
                run=latest,
                output_directory=output_directory,
                runtime_user=profile.runtime_user,
                resources=profile.resources,
                network=profile.network,
            )
            handle = await self._executor.start(request)
            current = await self._persist_executor_and_enter_running(
                claimed.id,
                handle.executor_id,
            )
            await self._append_event(
                claimed.id,
                "executor.started",
                {"executor_id": str(handle.executor_id)},
            )
            if current.state is RunState.RUNNING:
                await self._append_event(
                    claimed.id,
                    "run.running",
                    {"state": RunState.RUNNING.value},
                    created_at=current.started_at,
                )
            result = await self._wait_with_heartbeats(current, handle)
            await self._finalize_result(claimed.id, result)
        except asyncio.CancelledError:
            raise
        except _RunOwnershipLost:
            return
        except CloudError as exc:
            error_code = (
                exc.code.value
                if exc.code is CloudErrorCode.EXECUTOR_UNAVAILABLE
                else CloudErrorCode.EXECUTOR_FAILED.value
            )
            await self._fail_run(
                claimed.id,
                error_code=error_code,
                message="executor could not complete the run",
            )
        except Exception:  # noqa: BLE001 - executor boundary must terminalize failures
            await self._fail_run(
                claimed.id,
                error_code=CloudErrorCode.EXECUTOR_FAILED.value,
                message="executor could not complete the run",
            )

    async def _resume_run(self, adopted: Run) -> None:
        if adopted.executor_id is None:
            await self._fail_lease(adopted.id)
            return
        try:
            await self._set_workspace_state(adopted.workspace_id, WorkspaceState.BUSY)
            current = await self._persist_executor_and_enter_running(
                adopted.id,
                adopted.executor_id,
            )
            handle = ExecutorHandle(adopted.executor_id)
            if current.state is RunState.RUNNING and adopted.state is RunState.STARTING:
                await self._append_event(
                    adopted.id,
                    "run.running",
                    {"state": RunState.RUNNING.value, "reattached": True},
                    created_at=current.started_at,
                )
            result = await self._wait_with_heartbeats(current, handle)
            await self._finalize_result(adopted.id, result)
        except asyncio.CancelledError:
            raise
        except _RunOwnershipLost:
            return
        except Exception:  # noqa: BLE001 - executor boundary must terminalize failures
            await self._fail_run(
                adopted.id,
                error_code=CloudErrorCode.EXECUTOR_FAILED.value,
                message="reattached executor could not complete the run",
            )

    async def _wait_with_heartbeats(
        self,
        run: Run,
        handle: ExecutorHandle,
    ) -> ExecutionResult:
        async def publish(event: ExecutorEvent) -> None:
            await self._append_event(
                run.id,
                event.event_type,
                dict(event.payload),
                created_at=event.created_at,
            )

        wait_task = asyncio.create_task(self._executor.wait(handle, on_event=publish))
        cancellation_sent = False
        interval = self._settings.heartbeat_interval.total_seconds()
        try:
            while True:
                done, _ = await asyncio.wait({wait_task}, timeout=interval)
                if wait_task in done:
                    return wait_task.result()
                current = await self._get_run(run.id)
                if current.worker_id != self._settings.worker_id:
                    raise _RunOwnershipLost
                if current.state is RunState.CANCELLING and not cancellation_sent:
                    await self._executor.cancel(
                        handle.executor_id,
                        grace_period=_CANCELLATION_GRACE_PERIOD,
                    )
                    cancellation_sent = True
                    await self._append_event(
                        run.id,
                        "executor.cancellation_requested",
                        {"executor_id": str(handle.executor_id)},
                    )
                if current.state not in {
                    RunState.STARTING,
                    RunState.RUNNING,
                    RunState.CANCELLING,
                }:
                    raise CloudError(
                        CloudErrorCode.INVALID_TRANSITION,
                        "run became terminal while its executor was active",
                    )
                try:
                    await self._repository.heartbeat_run(
                        current.id,
                        worker_id=self._settings.worker_id,
                        expected_revision=current.revision,
                        lease_expires_at=self._clock() + self._settings.lease_duration,
                    )
                except CloudError as exc:
                    if exc.code is not CloudErrorCode.REVISION_CONFLICT:
                        raise
                    refreshed = await self._get_run(run.id)
                    if refreshed.worker_id != self._settings.worker_id:
                        raise _RunOwnershipLost from exc
        finally:
            if not wait_task.done():
                wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)

    async def _finalize_result(self, run_id: RunId, result: ExecutionResult) -> None:
        for run_file in result.files:
            if run_file.run_id != run_id:
                raise CloudError(
                    CloudErrorCode.INVALID_REQUEST,
                    "executor returned a file for a different run",
                )
            await self._repository.register_run_file(run_file)
        canonical_trajectories = tuple(
            run_file
            for run_file in result.files
            if run_file.kind is RunFileKind.TRAJECTORY
            and run_file.trajectory is not None
            and run_file.trajectory.role is TrajectoryRole.CANONICAL
            and run_file.trajectory.format is TrajectoryFormat.ATIF
        )
        for _ in range(10):
            current = await self._get_run(run_id)
            if current.state in {
                RunState.SUCCEEDED,
                RunState.FAILED,
                RunState.CANCELLED,
            }:
                await self._set_workspace_state(
                    current.workspace_id, WorkspaceState.READY
                )
                return
            if current.state is RunState.STARTING:
                running = transition_run(current, RunState.RUNNING, at=self._clock())
                try:
                    current = await self._repository.update_run(
                        running,
                        expected_revision=current.revision,
                        expected_state=RunState.STARTING,
                    )
                except CloudError as exc:
                    if exc.code is CloudErrorCode.REVISION_CONFLICT:
                        continue
                    raise
                await self._append_event(
                    run_id,
                    "run.running",
                    {"state": RunState.RUNNING.value, "recovered": True},
                    created_at=current.started_at,
                )
            if current.state is RunState.CANCELLING:
                target = RunState.CANCELLED
                error_code = result.error_code or "cancelled"
                message = "executor cancellation completed"
            elif current.mode is RunMode.QUERY and result.benchmark_outcome is not None:
                target = RunState.FAILED
                error_code = CloudErrorCode.EXECUTOR_FAILED.value
                message = "query executor returned benchmark-only output"
            elif (
                result.exit_code == 0
                and result.error_code is None
                and len(canonical_trajectories) == 1
            ):
                target = RunState.SUCCEEDED
                error_code = None
                message = None
            elif result.exit_code == 0 and result.error_code is None:
                target = RunState.FAILED
                error_code = CloudErrorCode.TRAJECTORY_MISSING.value
                message = (
                    "executor did not return exactly one canonical ATIF trajectory"
                )
            else:
                target = RunState.FAILED
                error_code = result.error_code or CloudErrorCode.EXECUTOR_FAILED.value
                message = "executor reported an unsuccessful result"
            terminal = transition_run(
                current,
                target,
                at=result.finished_at,
                exit_code=result.exit_code,
                error_code=error_code,
                error_message=message,
                benchmark_outcome=(
                    result.benchmark_outcome
                    if current.mode is RunMode.BENCHMARK
                    else None
                ),
            )
            try:
                stored = await self._repository.update_run(
                    terminal,
                    expected_revision=current.revision,
                    expected_state=current.state,
                )
            except CloudError as exc:
                if exc.code is CloudErrorCode.REVISION_CONFLICT:
                    continue
                raise
            await self._append_event(
                run_id,
                f"run.{stored.state.value}",
                {
                    "error_code": stored.error_code,
                    "exit_code": stored.exit_code,
                    "state": stored.state.value,
                },
                created_at=stored.finished_at,
            )
            await self._set_workspace_state(stored.workspace_id, WorkspaceState.READY)
            return
        raise CloudError(
            CloudErrorCode.REVISION_CONFLICT,
            "run changed repeatedly during finalization",
        )

    async def _finalize_cancelled(self, run_id: RunId) -> None:
        await self._finalize_result(
            run_id,
            ExecutionResult(
                exit_code=130,
                finished_at=self._clock(),
                error_code="cancelled",
                error_message="execution cancelled before start",
            ),
        )

    async def _fail_run(self, run_id: RunId, *, error_code: str, message: str) -> None:
        for _ in range(10):
            current = await self._repository.get_run(run_id)
            if current is None:
                return
            if current.state in {
                RunState.SUCCEEDED,
                RunState.FAILED,
                RunState.CANCELLED,
            }:
                await self._set_workspace_state(
                    current.workspace_id, WorkspaceState.READY
                )
                return
            if current.state is RunState.QUEUED:
                return
            failed = transition_run(
                current,
                RunState.FAILED,
                at=self._clock(),
                error_code=error_code,
                error_message=message,
            )
            try:
                stored = await self._repository.update_run(
                    failed,
                    expected_revision=current.revision,
                    expected_state=current.state,
                )
            except CloudError as exc:
                if exc.code is CloudErrorCode.REVISION_CONFLICT:
                    continue
                raise
            await self._append_event(
                run_id,
                "run.failed",
                {"error_code": error_code, "state": RunState.FAILED.value},
                created_at=stored.finished_at,
            )
            await self._set_workspace_state(stored.workspace_id, WorkspaceState.READY)
            return

    async def _fail_lease(self, run_id: RunId) -> None:
        await self._fail_run(
            run_id,
            error_code=CloudErrorCode.WORKER_LEASE_EXPIRED.value,
            message="worker lease expired and executor could not be reattached",
        )

    async def _adopt_run(self, run: Run) -> Run | None:
        adopted = replace(
            run,
            revision=run.revision + 1,
            worker_id=self._settings.worker_id,
            lease_expires_at=self._clock() + self._settings.lease_duration,
        )
        try:
            return await self._repository.update_run(
                adopted,
                expected_revision=run.revision,
                expected_state=run.state,
            )
        except CloudError as exc:
            if exc.code is CloudErrorCode.REVISION_CONFLICT:
                return None
            raise

    async def reconcile_startup(self) -> None:
        """Reconcile only expired executing work; queued work remains untouched."""

        expired = await self._repository.list_expired_runs(
            expired_before=self._clock(),
            limit=1000,
        )
        for run in expired:
            if run.executor_id is None:
                await self._fail_lease(run.id)
                continue
            try:
                inspection = await self._executor.inspect(run.executor_id)
            except Exception:  # noqa: BLE001 - failed inspection means no positive reattach
                await self._fail_lease(run.id)
                continue
            if (
                inspection.status is ExecutorStatus.EXITED
                and inspection.result is not None
            ):
                adopted = await self._adopt_run(run)
                if adopted is None:
                    continue
                await self._append_event(
                    run.id,
                    "executor.recovered_result",
                    {"executor_id": str(run.executor_id)},
                )
                await self._finalize_result(run.id, inspection.result)
                continue
            if inspection.status is ExecutorStatus.RUNNING and inspection.reattachable:
                adopted = await self._adopt_run(run)
                if adopted is None:
                    continue
                await self._append_event(
                    run.id,
                    "executor.reattached",
                    {
                        "executor_id": str(run.executor_id),
                        "worker_id": self._settings.worker_id,
                    },
                )
                self._schedule(adopted, reattached=True)
                continue
            await self._fail_lease(run.id)
