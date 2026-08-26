from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aworld.cloud.errors import CloudErrorCode
from aworld.cloud.fake_executor import FakeCloudExecutor, FakeExecutionPlan
from aworld.cloud.models import RunState
from aworld.cloud.service import CloudService
from aworld.cloud.sqlite_repository import SQLiteCloudRepository
from aworld.cloud.worker import CloudWorker
from tests.cloud.test_service_worker import (
    _database_path,
    _SequentialIds,
    _settings,
    _wait_for_run_state,
)


@pytest.mark.asyncio
async def test_restart_leaves_queued_work_claimable_without_resubmission(
    tmp_path: Path,
) -> None:
    settings, _ = _settings(tmp_path, worker_id="worker-before-restart")
    first_repository = SQLiteCloudRepository(_database_path(settings))
    await first_repository.initialize()
    service = CloudService(first_repository, settings, id_factory=_SequentialIds())
    workspace = await service.create_workspace(
        name="Queued recovery",
        profile_name="aworld-development",
        idempotency_key="workspace-key",
    )
    run = await service.submit_run(
        workspace.workspace.id,
        task="queued-recovery",
        model=None,
        idempotency_key="run-key",
    )
    await first_repository.close()

    restarted_settings, _ = _settings(tmp_path, worker_id="worker-after-restart")
    restarted_repository = SQLiteCloudRepository(_database_path(restarted_settings))
    await restarted_repository.initialize()
    executor = FakeCloudExecutor()
    worker = CloudWorker(restarted_repository, executor, restarted_settings)
    await worker.reconcile_startup()
    queued = await restarted_repository.get_run(run.id)
    assert queued is not None
    assert queued.state is RunState.QUEUED
    await worker.run_until_idle()
    terminal = await restarted_repository.get_run(run.id)
    assert terminal is not None
    assert terminal.state is RunState.SUCCEEDED
    assert executor.start_calls == [run.id]
    await restarted_repository.close()


async def _start_interrupted_run(
    tmp_path: Path,
    *,
    reattachable: bool,
):
    settings, _ = _settings(tmp_path, worker_id="worker-before-restart")
    repository = SQLiteCloudRepository(_database_path(settings))
    await repository.initialize()
    service = CloudService(repository, settings, id_factory=_SequentialIds())
    executor = FakeCloudExecutor()
    workspace = await service.create_workspace(
        name="Interrupted",
        profile_name="aworld-development",
        idempotency_key="workspace-key",
    )
    run = await service.submit_run(
        workspace.workspace.id,
        task="interrupted",
        model=None,
        idempotency_key="run-key",
    )
    executor.set_plan(
        run.id,
        FakeExecutionPlan(
            block_until_released=True,
            reattachable=reattachable,
        ),
    )
    worker = CloudWorker(repository, executor, settings)
    await worker.run_once()
    handle = await executor.wait_until_started(run.id)
    running = await _wait_for_run_state(service, run.id, {RunState.RUNNING})
    await worker.stop(graceful=False)
    await repository.close()
    await asyncio.sleep(0.08)
    return settings, executor, run, running, handle


@pytest.mark.asyncio
async def test_positive_reattachment_finishes_without_replaying_start(
    tmp_path: Path,
) -> None:
    _, executor, run, interrupted, _ = await _start_interrupted_run(
        tmp_path,
        reattachable=True,
    )
    restarted_settings, _ = _settings(tmp_path, worker_id="worker-after-restart")
    repository = SQLiteCloudRepository(_database_path(restarted_settings))
    await repository.initialize()
    worker = CloudWorker(repository, executor, restarted_settings)

    await worker.reconcile_startup()
    adopted = await repository.get_run(run.id)
    assert adopted is not None
    assert adopted.worker_id == "worker-after-restart"
    assert adopted.revision > interrupted.revision
    executor.release(run.id)
    await worker.wait_for_idle()

    terminal = await repository.get_run(run.id)
    events = await repository.list_events(run.id, limit=50)
    assert terminal is not None
    assert terminal.state is RunState.SUCCEEDED
    assert executor.start_calls == [run.id]
    assert "executor.reattached" in [event.event_type for event in events.items]
    await repository.close()


@pytest.mark.asyncio
async def test_unreattachable_expired_run_fails_without_blind_replay(
    tmp_path: Path,
) -> None:
    _, executor, run, _, handle = await _start_interrupted_run(
        tmp_path,
        reattachable=False,
    )
    restarted_settings, _ = _settings(tmp_path, worker_id="worker-after-restart")
    repository = SQLiteCloudRepository(_database_path(restarted_settings))
    await repository.initialize()
    worker = CloudWorker(repository, executor, restarted_settings)

    await worker.reconcile_startup()

    terminal = await repository.get_run(run.id)
    assert terminal is not None
    assert terminal.state is RunState.FAILED
    assert terminal.error_code == CloudErrorCode.WORKER_LEASE_EXPIRED.value
    assert executor.start_calls == [run.id]
    await executor.cancel(
        handle.executor_id, grace_period=restarted_settings.heartbeat_interval
    )
    await repository.close()


@pytest.mark.asyncio
async def test_multiple_workers_contend_but_only_one_starts_executor(
    tmp_path: Path,
) -> None:
    first_settings, _ = _settings(tmp_path, worker_id="worker-1")
    second_settings, _ = _settings(tmp_path, worker_id="worker-2")
    first_repository = SQLiteCloudRepository(_database_path(first_settings))
    second_repository = SQLiteCloudRepository(_database_path(second_settings))
    await first_repository.initialize()
    await second_repository.initialize()
    service = CloudService(
        first_repository, first_settings, id_factory=_SequentialIds()
    )
    executor = FakeCloudExecutor()
    workspace = await service.create_workspace(
        name="Contended",
        profile_name="aworld-development",
        idempotency_key="workspace-key",
    )
    run = await service.submit_run(
        workspace.workspace.id,
        task="contended-run",
        model=None,
        idempotency_key="run-key",
    )
    executor.set_plan(run.id, FakeExecutionPlan(block_until_released=True))
    first_worker = CloudWorker(first_repository, executor, first_settings)
    second_worker = CloudWorker(second_repository, executor, second_settings)

    claims = await asyncio.gather(first_worker.run_once(), second_worker.run_once())
    await executor.wait_until_started(run.id)
    assert sorted(claims) == [0, 1]
    assert executor.start_calls == [run.id]
    executor.release(run.id)
    await asyncio.gather(first_worker.wait_for_idle(), second_worker.wait_for_idle())
    terminal = await first_repository.get_run(run.id)
    assert terminal is not None
    assert terminal.state is RunState.SUCCEEDED
    await first_repository.close()
    await second_repository.close()
