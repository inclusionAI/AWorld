from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath

import pytest

from aworld.cloud.errors import CloudError, CloudErrorCode, WorkspaceBusyError
from aworld.cloud.fake_executor import (
    FakeCloudExecutor,
    FakeEventSpec,
    FakeExecutionPlan,
)
from aworld.cloud.models import (
    BenchmarkMetadata,
    BenchmarkOutcome,
    FileId,
    Run,
    RunFile,
    RunFileKind,
    RunId,
    RunMode,
    RunState,
    TrajectoryFormat,
    TrajectoryManifest,
    TrajectoryRole,
    WorkspaceId,
    WorkspaceState,
    utc_now,
)
from aworld.cloud.service import CloudService
from aworld.cloud.settings import (
    CloudSettings,
    NetworkPolicy,
    ReferenceRepository,
    ResourceLimits,
    WorkspaceProfile,
)
from aworld.cloud.sqlite_repository import SQLiteCloudRepository
from aworld.cloud.worker import CloudWorker


@dataclass
class _SequentialIds:
    value: int = 0

    def __call__(self) -> str:
        self.value += 1
        return f"id-{self.value}"


@dataclass
class _Stack:
    settings: CloudSettings
    repository: SQLiteCloudRepository
    service: CloudService
    executor: FakeCloudExecutor
    worker: CloudWorker
    reference_path: Path


def _database_path(settings: CloudSettings) -> Path:
    assert settings.database_path is not None
    return settings.database_path


def _settings(
    tmp_path: Path,
    *,
    worker_id: str = "worker-1",
    concurrency: int = 1,
) -> tuple[CloudSettings, Path]:
    reference_path = tmp_path / "administrator-reference" / "gateway"
    profile = WorkspaceProfile(
        name="aworld-development",
        writable_repo_root=tmp_path / "writable-workspaces",
        runtime_image="registry.example/codex@sha256:abc",
        references=(
            ReferenceRepository(
                name="gateway",
                host_path=reference_path,
                container_path=PurePosixPath("/workspace/reference/gateway"),
            ),
        ),
        resources=ResourceLimits(
            cpus=1,
            memory_bytes=256 * 1024 * 1024,
            pids=64,
            wall_clock_timeout=timedelta(seconds=30),
        ),
        network=NetworkPolicy(mode="none"),
    )
    settings = CloudSettings(
        enabled=True,
        data_root=tmp_path / "cloud-data",
        database_path=tmp_path / "cloud-data" / "cloud.sqlite3",
        worker_id=worker_id,
        concurrency=concurrency,
        lease_duration=timedelta(milliseconds=60),
        heartbeat_interval=timedelta(milliseconds=10),
        poll_interval=timedelta(milliseconds=5),
        allowed_profiles={profile.name: profile},
        allowed_images=frozenset({profile.runtime_image}),
    )
    return settings, reference_path


async def _stack(tmp_path: Path, *, concurrency: int = 1) -> _Stack:
    settings, reference_path = _settings(tmp_path, concurrency=concurrency)
    repository = SQLiteCloudRepository(_database_path(settings))
    await repository.initialize()
    ids = _SequentialIds()
    service = CloudService(repository, settings, id_factory=ids)
    executor = FakeCloudExecutor()
    worker = CloudWorker(repository, executor, settings)
    return _Stack(
        settings=settings,
        repository=repository,
        service=service,
        executor=executor,
        worker=worker,
        reference_path=reference_path,
    )


async def _create_workspace(stack: _Stack, name: str = "Workspace") -> WorkspaceId:
    inspection = await stack.service.create_workspace(
        name=name,
        profile_name="aworld-development",
        idempotency_key=f"workspace-key-{name}",
    )
    return inspection.workspace.id


async def _submit(stack: _Stack, workspace_id: WorkspaceId, task: str) -> Run:
    return await stack.service.submit_run(
        workspace_id,
        task=task,
        model="codex-test",
        idempotency_key=f"run-key-{task}",
    )


async def _wait_for_run_state(
    service: CloudService,
    run_id: RunId,
    states: set[RunState],
) -> Run:
    for _ in range(200):
        run = await service.get_run(run_id)
        if run.state in states:
            return run
        await asyncio.sleep(0.002)
    raise AssertionError(f"run {run_id} did not reach {states}")


@pytest.mark.asyncio
async def test_workspace_service_resolves_profile_inspects_and_releases(
    tmp_path: Path,
) -> None:
    stack = await _stack(tmp_path)
    created = await stack.service.create_workspace(
        name="Primary",
        profile_name="aworld-development",
        idempotency_key="workspace-create",
    )
    workspace = created.workspace
    (workspace.codex_home_path / "config.toml").write_text(
        "model='test'", encoding="utf-8"
    )
    (workspace.codex_home_path / "auth.json").write_text("{}", encoding="utf-8")

    repeated = await stack.service.create_workspace(
        name="Primary",
        profile_name="aworld-development",
        idempotency_key="workspace-create",
    )
    inspected = await stack.service.inspect_workspace(workspace.id)
    listed = await stack.service.list_workspaces(limit=10)

    assert repeated.workspace.id == workspace.id
    assert inspected.codex_config_present is True
    assert inspected.codex_auth_present is True
    assert listed.items[0].workspace.id == workspace.id
    assert workspace.writable_repo_path.is_dir()
    assert stack.reference_path.exists() is False
    assert [mount.access_mode.value for mount in workspace.mounts] == ["rw", "ro", "rw"]

    released = await stack.service.release_workspace(
        workspace.id,
        idempotency_key="release-workspace",
    )
    repeated_release = await stack.service.release_workspace(
        workspace.id,
        idempotency_key="release-workspace",
    )
    assert released.workspace.state is WorkspaceState.RELEASED
    assert repeated_release.workspace == released.workspace
    assert workspace.writable_repo_path.exists() is False
    assert workspace.codex_home_path.is_dir()

    secondary = await stack.service.create_workspace(
        name="Secondary",
        profile_name="aworld-development",
        idempotency_key="workspace-secondary",
    )
    with pytest.raises(CloudError) as release_conflict:
        await stack.service.release_workspace(
            secondary.workspace.id,
            idempotency_key="release-workspace",
        )
    assert release_conflict.value.code is CloudErrorCode.IDEMPOTENCY_CONFLICT
    await stack.repository.close()


@pytest.mark.asyncio
async def test_successful_worker_execution_persists_events_result_and_files(
    tmp_path: Path,
) -> None:
    stack = await _stack(tmp_path)
    workspace_id = await _create_workspace(stack)
    run = await _submit(stack, workspace_id, "successful")
    run_file = RunFile(
        id=FileId("file-success"),
        run_id=run.id,
        kind=RunFileKind.RESULT,
        relative_path=PurePosixPath("result.json"),
        size_bytes=2,
        sha256="a" * 64,
        created_at=utc_now(),
    )
    stack.executor.set_plan(
        run.id,
        FakeExecutionPlan(
            events=(FakeEventSpec("executor.progress", {"step": 1}),),
            files=(run_file,),
        ),
    )

    await stack.worker.run_until_idle()

    terminal = await stack.service.get_run(run.id)
    workspace = await stack.service.inspect_workspace(workspace_id)
    events = await stack.repository.list_events(run.id, limit=50)
    assert terminal.state is RunState.SUCCEEDED
    assert terminal.started_at is not None
    assert terminal.finished_at is not None
    assert terminal.executor_id is not None
    assert terminal.exit_code == 0
    assert workspace.workspace.state is WorkspaceState.READY
    assert workspace.active_run_id is None
    assert [event.sequence for event in events.items] == list(
        range(1, len(events.items) + 1)
    )
    assert [event.event_type for event in events.items] == [
        "run.queued",
        "run.starting",
        "executor.started",
        "run.running",
        "executor.progress",
        "run.succeeded",
    ]
    files = await stack.repository.list_run_files(run.id)
    assert files[0] == run_file
    assert files[1].kind is RunFileKind.TRAJECTORY
    assert files[1].trajectory is not None
    assert files[1].trajectory.schema_version == "ATIF-v1.7"
    await stack.repository.close()


@pytest.mark.asyncio
async def test_success_without_canonical_trajectory_fails_contract(
    tmp_path: Path,
) -> None:
    stack = await _stack(tmp_path)
    workspace_id = await _create_workspace(stack)
    run = await _submit(stack, workspace_id, "missing trajectory")
    stack.executor.set_plan(
        run.id,
        FakeExecutionPlan(emit_canonical_trajectory=False),
    )

    await stack.worker.run_until_idle()

    terminal = await stack.service.get_run(run.id)
    assert terminal.state is RunState.FAILED
    assert terminal.error_code == CloudErrorCode.TRAJECTORY_MISSING.value
    await stack.repository.close()


@pytest.mark.asyncio
async def test_provider_raw_trajectory_is_preserved_with_canonical_atif(
    tmp_path: Path,
) -> None:
    stack = await _stack(tmp_path)
    workspace_id = await _create_workspace(stack)
    run = await _submit(stack, workspace_id, "raw provider trajectory")
    raw = RunFile(
        id=FileId("file-provider-raw"),
        run_id=run.id,
        kind=RunFileKind.TRAJECTORY,
        relative_path=PurePosixPath("provider/trajectory.jsonl"),
        size_bytes=12,
        sha256="b" * 64,
        created_at=utc_now(),
        trajectory=TrajectoryManifest(
            format=TrajectoryFormat.PROVIDER_NATIVE,
            schema_version="open-sandbox.events.v1",
            role=TrajectoryRole.PROVIDER_RAW,
        ),
    )
    stack.executor.set_plan(run.id, FakeExecutionPlan(files=(raw,)))

    await stack.worker.run_until_idle()

    terminal = await stack.service.get_run(run.id)
    files = await stack.repository.list_run_files(run.id)
    assert terminal.state is RunState.SUCCEEDED
    assert {run_file.trajectory.role for run_file in files} == {
        TrajectoryRole.CANONICAL,
        TrajectoryRole.PROVIDER_RAW,
    }
    assert raw in files
    await stack.repository.close()


@pytest.mark.asyncio
async def test_benchmark_terminal_outcome_is_persisted(tmp_path: Path) -> None:
    stack = await _stack(tmp_path)
    workspace_id = await _create_workspace(stack)
    run = await stack.service.submit_run(
        workspace_id,
        task="benchmark task",
        model="codex-test",
        idempotency_key="benchmark-run",
        mode=RunMode.BENCHMARK,
        benchmark=BenchmarkMetadata(
            dataset="swe-bench",
            task_id="case-1",
            harness="harness-v1",
            verifier="verifier-v1",
        ),
    )
    outcome = BenchmarkOutcome(
        reward=0.75,
        result={"passed": True, "details": {"checks": 4}},
    )
    stack.executor.set_plan(run.id, FakeExecutionPlan(benchmark_outcome=outcome))

    await stack.worker.run_until_idle()

    terminal = await stack.service.get_run(run.id)
    assert terminal.state is RunState.SUCCEEDED
    assert terminal.benchmark_outcome == outcome
    await stack.repository.close()


@pytest.mark.asyncio
async def test_query_rejects_benchmark_only_executor_output(tmp_path: Path) -> None:
    stack = await _stack(tmp_path)
    workspace_id = await _create_workspace(stack)
    run = await _submit(stack, workspace_id, "query with benchmark output")
    stack.executor.set_plan(
        run.id,
        FakeExecutionPlan(benchmark_outcome=BenchmarkOutcome(reward=1.0)),
    )

    await stack.worker.run_until_idle()

    terminal = await stack.service.get_run(run.id)
    assert terminal.state is RunState.FAILED
    assert terminal.error_code == CloudErrorCode.EXECUTOR_FAILED.value
    assert terminal.benchmark_outcome is None
    await stack.repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plan", "expected_code"),
    [
        (
            FakeExecutionPlan(start_failure=True),
            CloudErrorCode.EXECUTOR_UNAVAILABLE.value,
        ),
        (
            FakeExecutionPlan(exit_code=17),
            CloudErrorCode.EXECUTOR_FAILED.value,
        ),
    ],
)
async def test_worker_terminalizes_start_and_execution_failures(
    tmp_path: Path,
    plan: FakeExecutionPlan,
    expected_code: str,
) -> None:
    stack = await _stack(tmp_path)
    workspace_id = await _create_workspace(stack)
    run = await _submit(stack, workspace_id, expected_code)
    stack.executor.set_plan(run.id, plan)

    await stack.worker.run_until_idle()

    terminal = await stack.service.get_run(run.id)
    assert terminal.state is RunState.FAILED
    assert terminal.error_code == expected_code
    assert terminal.error_message == "executor could not complete the run" or (
        terminal.error_message == "executor reported an unsuccessful result"
    )
    assert (
        await stack.service.inspect_workspace(workspace_id)
    ).workspace.state is WorkspaceState.READY
    await stack.repository.close()


@pytest.mark.asyncio
async def test_queued_and_executing_cancellation_are_idempotent(tmp_path: Path) -> None:
    stack = await _stack(tmp_path)
    queued_workspace = await _create_workspace(stack, "Queued")
    queued = await _submit(stack, queued_workspace, "queued-cancel")

    cancelled_queued = await stack.service.cancel_run(
        queued.id,
        idempotency_key="cancel-queued",
    )
    repeated = await stack.service.cancel_run(
        queued.id,
        idempotency_key="cancel-queued",
    )
    assert cancelled_queued.state is RunState.CANCELLED
    assert repeated == cancelled_queued
    assert await stack.worker.run_once() == 0

    executing_workspace = await _create_workspace(stack, "Executing")
    executing = await _submit(stack, executing_workspace, "executing-cancel")
    stack.executor.set_plan(
        executing.id,
        FakeExecutionPlan(wait_for_cancellation=True),
    )
    assert await stack.worker.run_once() == 1
    handle = await stack.executor.wait_until_started(executing.id)
    await asyncio.sleep(0.025)
    heartbeat = await stack.service.get_run(executing.id)
    assert heartbeat.revision >= 4
    assert heartbeat.lease_expires_at is not None
    await stack.service.cancel_run(
        executing.id,
        idempotency_key="cancel-executing",
    )
    await stack.worker.wait_for_idle()

    terminal = await stack.service.get_run(executing.id)
    assert terminal.state is RunState.CANCELLED
    assert handle.executor_id in stack.executor.cancel_calls
    assert (
        await stack.service.inspect_workspace(executing_workspace)
    ).workspace.state is WorkspaceState.READY
    await stack.repository.close()


@pytest.mark.asyncio
async def test_failed_run_retry_preserves_lineage_and_original(tmp_path: Path) -> None:
    stack = await _stack(tmp_path)
    workspace_id = await _create_workspace(stack)
    original = await _submit(stack, workspace_id, "retry-me")
    stack.executor.set_plan(original.id, FakeExecutionPlan(exit_code=1))
    await stack.worker.run_until_idle()
    failed = await stack.service.get_run(original.id)

    retry = await stack.service.retry_run(
        failed.id,
        idempotency_key="retry-key",
    )
    repeated = await stack.service.retry_run(
        failed.id,
        idempotency_key="retry-key",
    )
    await stack.worker.run_until_idle()

    assert failed.state is RunState.FAILED
    assert retry.attempt == 2
    assert retry.retry_of_run_id == failed.id
    assert repeated.id == retry.id
    assert (await stack.service.get_run(retry.id)).state is RunState.SUCCEEDED
    assert (await stack.service.get_run(failed.id)) == failed
    await stack.repository.close()


@pytest.mark.asyncio
async def test_worker_capacity_leaves_excess_runs_durably_queued(
    tmp_path: Path,
) -> None:
    stack = await _stack(tmp_path, concurrency=2)
    runs: list[Run] = []
    for index in range(3):
        workspace_id = await _create_workspace(stack, f"Capacity-{index}")
        run = await _submit(stack, workspace_id, f"capacity-{index}")
        stack.executor.set_plan(
            run.id,
            FakeExecutionPlan(block_until_released=True),
        )
        runs.append(run)

    assert await stack.worker.run_once() == 2
    await asyncio.gather(
        stack.executor.wait_until_started(runs[0].id),
        stack.executor.wait_until_started(runs[1].id),
    )
    assert stack.executor.max_active == 2
    assert (await stack.service.get_run(runs[2].id)).state is RunState.QUEUED

    stack.executor.release(runs[0].id)
    stack.executor.release(runs[1].id)
    await stack.worker.wait_for_idle()
    assert await stack.worker.run_once() == 1
    await stack.executor.wait_until_started(runs[2].id)
    stack.executor.release(runs[2].id)
    await stack.worker.wait_for_idle()
    terminal_runs = [await stack.service.get_run(run.id) for run in runs]
    assert all(run.state is RunState.SUCCEEDED for run in terminal_runs)
    await stack.repository.close()


@pytest.mark.asyncio
async def test_release_and_submit_reject_busy_workspace(tmp_path: Path) -> None:
    stack = await _stack(tmp_path)
    workspace_id = await _create_workspace(stack)
    run = await _submit(stack, workspace_id, "busy")
    stack.executor.set_plan(run.id, FakeExecutionPlan(block_until_released=True))
    await stack.worker.run_once()
    await stack.executor.wait_until_started(run.id)
    await _wait_for_run_state(stack.service, run.id, {RunState.RUNNING})

    repeated = await stack.service.submit_run(
        workspace_id,
        task="busy",
        model="codex-test",
        idempotency_key="run-key-busy",
    )
    assert repeated.id == run.id

    with pytest.raises(WorkspaceBusyError):
        await stack.service.submit_run(
            workspace_id,
            task="second",
            model=None,
            idempotency_key="second-key",
        )
    with pytest.raises(WorkspaceBusyError):
        await stack.service.release_workspace(
            workspace_id,
            idempotency_key="release-busy",
        )

    stack.executor.release(run.id)
    await stack.worker.wait_for_idle()
    await stack.repository.close()


@pytest.mark.asyncio
async def test_invalid_profile_and_changed_idempotent_payload_are_rejected(
    tmp_path: Path,
) -> None:
    stack = await _stack(tmp_path)
    with pytest.raises(CloudError) as missing_profile:
        await stack.service.create_workspace(
            name="Missing",
            profile_name="client-profile",
            idempotency_key="missing-profile",
        )
    assert missing_profile.value.code is CloudErrorCode.PROFILE_NOT_FOUND

    await stack.service.create_workspace(
        name="Stable",
        profile_name="aworld-development",
        idempotency_key="stable-key",
    )
    with pytest.raises(CloudError) as conflict:
        await stack.service.create_workspace(
            name="Changed",
            profile_name="aworld-development",
            idempotency_key="stable-key",
        )
    assert conflict.value.code is CloudErrorCode.IDEMPOTENCY_CONFLICT
    await stack.repository.close()


@pytest.mark.asyncio
async def test_service_rejects_unsafe_generated_workspace_path(tmp_path: Path) -> None:
    settings, _ = _settings(tmp_path)
    repository = SQLiteCloudRepository(_database_path(settings))
    await repository.initialize()
    service = CloudService(repository, settings, id_factory=lambda: "../escape")

    with pytest.raises(CloudError) as raised:
        await service.create_workspace(
            name="Unsafe",
            profile_name="aworld-development",
            idempotency_key="unsafe-key",
        )

    assert raised.value.code is CloudErrorCode.UNSAFE_MOUNT
    assert (tmp_path / "escape").exists() is False
    await repository.close()
