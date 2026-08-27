from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

from aworld.cloud.errors import CloudError, CloudErrorCode
from aworld.cloud.models import (
    ATIF_SCHEMA_VERSION,
    BatchId,
    BatchState,
    BenchmarkMetadata,
    BenchmarkOutcome,
    FileId,
    MountAccessMode,
    Run,
    RunFile,
    RunFileKind,
    RunId,
    RunMode,
    RunState,
    TrajectoryFormat,
    TrajectoryManifest,
    TrajectoryRole,
    Workspace,
    WorkspaceId,
    WorkspaceMount,
    WorkspaceState,
    aggregate_batch,
    create_retry_run,
    transition_run,
    transition_workspace,
)
from aworld.cloud.sqlite_repository import (
    _SCHEMA_V1,
    SCHEMA_VERSION,
    SQLiteCloudRepository,
)

NOW = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _workspace(
    workspace_id: str = "workspace-1",
    *,
    created_at: datetime = NOW,
) -> Workspace:
    return Workspace(
        id=WorkspaceId(workspace_id),
        name=f"Workspace {workspace_id}",
        profile_name="aworld-development",
        state=WorkspaceState.READY,
        revision=1,
        runtime_image="registry.example/codex@sha256:abc",
        writable_repo_path=Path(f"/srv/aworld/workspaces/{workspace_id}"),
        codex_home_path=Path(f"/srv/aworld/codex/{workspace_id}"),
        workdir=PurePosixPath("/workspace/aworld"),
        mounts=(
            WorkspaceMount(
                host_path=Path(f"/srv/aworld/workspaces/{workspace_id}"),
                container_path=PurePosixPath("/workspace/aworld"),
                access_mode=MountAccessMode.READ_WRITE,
            ),
            WorkspaceMount(
                host_path=Path("/srv/reference/gateway"),
                container_path=PurePosixPath("/workspace/reference/gateway"),
                access_mode=MountAccessMode.READ_ONLY,
            ),
        ),
        created_at=created_at,
        updated_at=created_at,
    )


def _run(
    run_id: str = "run-1",
    *,
    workspace_id: str = "workspace-1",
    created_at: datetime = NOW,
) -> Run:
    return Run(
        id=RunId(run_id),
        workspace_id=WorkspaceId(workspace_id),
        state=RunState.QUEUED,
        revision=0,
        attempt=1,
        task=f"Execute task {run_id}",
        model="codex-test",
        created_at=created_at,
    )


async def _repository(database_path: Path) -> SQLiteCloudRepository:
    repository = SQLiteCloudRepository(database_path)
    await repository.initialize()
    return repository


async def _store_workspace(
    repository: SQLiteCloudRepository,
    workspace: Workspace,
) -> Workspace:
    return await repository.create_workspace(
        workspace,
        idempotency_key=f"create-{workspace.id}",
        request_fingerprint=f"fingerprint-{workspace.id}",
    )


async def _store_run(repository: SQLiteCloudRepository, run: Run) -> Run:
    return await repository.create_run(
        run,
        idempotency_key=f"create-{run.id}",
        request_fingerprint=f"fingerprint-{run.id}",
    )


def _batch_runs() -> tuple[Run, ...]:
    batch_id = BatchId("batch-1")
    return (
        replace(_run("run-batch-1"), batch_id=batch_id),
        replace(
            _run("run-batch-2", created_at=NOW + timedelta(seconds=1)),
            batch_id=batch_id,
        ),
    )


@pytest.mark.asyncio
async def test_schema_initialization_is_versioned_idempotent_and_configured(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "nested" / "cloud.sqlite3"
    repository = SQLiteCloudRepository(
        database_path,
        busy_timeout=timedelta(milliseconds=275),
    )

    await repository.initialize()
    await repository.initialize()

    connection = repository._connection
    assert connection is not None
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 275
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert (
        connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3
    )
    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "workspaces",
        "workspace_mounts",
        "runs",
        "batches",
        "run_events",
        "run_files",
        "idempotency_keys",
    } <= table_names
    await repository.close()


@pytest.mark.asyncio
async def test_schema_v1_rolls_forward_with_query_defaults(tmp_path: Path) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for statement in _SCHEMA_V1:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
            (NOW.isoformat(),),
        )
        connection.execute(
            """
            INSERT INTO workspaces(
                id, name, profile_name, state, revision, runtime_image,
                writable_repo_path, codex_home_path, workdir,
                created_at, updated_at, released_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "workspace-1",
                "Legacy workspace",
                "aworld-development",
                "ready",
                1,
                "registry.example/codex@sha256:abc",
                "/srv/aworld/workspaces/workspace-1",
                "/srv/aworld/codex/workspace-1",
                "/workspace/aworld",
                NOW.isoformat(),
                NOW.isoformat(),
                None,
            ),
        )
        connection.execute(
            """
            INSERT INTO runs(
                id, workspace_id, state, revision, attempt, retry_of_run_id,
                task, model, worker_id, lease_expires_at, executor_id,
                created_at, started_at, finished_at, exit_code,
                error_code, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-legacy",
                "workspace-1",
                "queued",
                0,
                1,
                None,
                "legacy query",
                None,
                None,
                None,
                None,
                NOW.isoformat(),
                None,
                None,
                None,
                None,
                None,
            ),
        )

    repository = await _repository(database_path)
    stored = await repository.get_run(RunId("run-legacy"))

    assert stored is not None
    assert stored.mode is RunMode.QUERY
    assert stored.benchmark is None
    assert stored.batch_id is None
    assert repository._connection is not None
    assert repository._connection.execute("PRAGMA user_version").fetchone()[0] == 3
    await repository.close()


@pytest.mark.asyncio
async def test_batch_create_list_and_cancel_are_atomic_and_run_derived(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path / "cloud.sqlite3")
    await _store_workspace(repository, _workspace())
    runs = _batch_runs()
    candidate = aggregate_batch(
        batch_id=BatchId("batch-1"),
        workspace_id=WorkspaceId("workspace-1"),
        name="nightly",
        created_at=NOW,
        runs=runs,
    )

    created = await repository.create_batch(
        candidate,
        runs,
        idempotency_key="batch-create",
        request_fingerprint="batch-fingerprint",
    )
    repeated = await repository.create_batch(
        replace(candidate, id=BatchId("batch-unused")),
        tuple(replace(run, batch_id=BatchId("batch-unused")) for run in runs),
        idempotency_key="batch-create",
        request_fingerprint="batch-fingerprint",
    )
    page = await repository.list_batch_runs(BatchId("batch-1"), limit=1)
    second_page = await repository.list_batch_runs(
        BatchId("batch-1"), limit=1, page_token=page.next_page_token
    )

    assert created.state is BatchState.QUEUED
    assert created.counts.total == 2
    assert repeated.id == created.id
    assert [run.id for run in page.items + second_page.items] == [
        RunId("run-batch-1"),
        RunId("run-batch-2"),
    ]
    assert all(run.batch_id == BatchId("batch-1") for run in page.items)

    cancelled = await repository.cancel_batch(
        BatchId("batch-1"),
        requested_at=NOW + timedelta(seconds=2),
        idempotency_key="batch-cancel",
        request_fingerprint="batch-cancel-fingerprint",
    )
    assert cancelled.state is BatchState.CANCELLED
    assert cancelled.counts.cancelled == 2
    assert cancelled.progress == 1.0
    events = await repository.list_events(RunId("run-batch-1"), limit=10)
    assert [event.event_type for event in events.items] == [
        "run.queued",
        "run.cancelled",
    ]
    await repository.close()


@pytest.mark.asyncio
async def test_benchmark_run_fields_survive_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    repository = await _repository(database_path)
    await _store_workspace(repository, _workspace())
    benchmark_run = replace(
        _run(),
        mode=RunMode.BENCHMARK,
        benchmark=BenchmarkMetadata(
            dataset="swe-bench",
            task_id="case-1",
            harness="custom",
            verifier="patch",
        ),
    )
    stored = await _store_run(repository, benchmark_run)
    starting = transition_run(stored, RunState.STARTING, at=NOW)
    starting = await repository.update_run(
        starting,
        expected_revision=stored.revision,
        expected_state=RunState.QUEUED,
    )
    running = transition_run(starting, RunState.RUNNING, at=NOW)
    running = await repository.update_run(
        running,
        expected_revision=starting.revision,
        expected_state=RunState.STARTING,
    )
    terminal = transition_run(
        running,
        RunState.SUCCEEDED,
        at=NOW,
        exit_code=0,
        benchmark_outcome=BenchmarkOutcome(
            reward=0.75,
            result={"passed": True, "checks": ["patch", "tests"]},
        ),
    )
    terminal = await repository.update_run(
        terminal,
        expected_revision=running.revision,
        expected_state=RunState.RUNNING,
    )
    await repository.close()

    restarted = await _repository(database_path)
    assert await restarted.get_run(benchmark_run.id) == terminal
    await restarted.close()


@pytest.mark.asyncio
async def test_workspace_persistence_mounts_pagination_and_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    repository = await _repository(database_path)
    for index in range(3):
        await _store_workspace(
            repository,
            _workspace(
                f"workspace-{index}",
                created_at=NOW + timedelta(seconds=index),
            ),
        )

    first_page = await repository.list_workspaces(limit=2)
    assert [str(item.id) for item in first_page.items] == [
        "workspace-0",
        "workspace-1",
    ]
    assert first_page.next_page_token is not None
    assert "workspace-1" not in first_page.next_page_token
    second_page = await repository.list_workspaces(
        limit=2,
        page_token=first_page.next_page_token,
    )
    assert [str(item.id) for item in second_page.items] == ["workspace-2"]
    assert second_page.next_page_token is None

    await repository.close()
    restarted = await _repository(database_path)
    restored = await restarted.get_workspace(WorkspaceId("workspace-1"))
    assert restored is not None
    assert (
        restored.mounts
        == _workspace("workspace-1", created_at=NOW + timedelta(seconds=1)).mounts
    )
    assert restored.created_at.utcoffset() == timedelta(0)
    await restarted.close()


@pytest.mark.asyncio
async def test_workspace_idempotency_and_conflict_detection(tmp_path: Path) -> None:
    repository = await _repository(tmp_path / "cloud.sqlite3")
    first = _workspace("workspace-first")

    stored = await repository.create_workspace(
        first,
        idempotency_key="same-key",
        request_fingerprint="same-request",
    )
    repeated = await repository.create_workspace(
        _workspace("workspace-not-created"),
        idempotency_key="same-key",
        request_fingerprint="same-request",
    )

    assert repeated == stored
    assert await repository.get_workspace(WorkspaceId("workspace-not-created")) is None
    with pytest.raises(CloudError) as raised:
        await repository.create_workspace(
            _workspace("workspace-conflict"),
            idempotency_key="same-key",
            request_fingerprint="different-request",
        )
    assert raised.value.code is CloudErrorCode.IDEMPOTENCY_CONFLICT
    await repository.close()


@pytest.mark.asyncio
async def test_workspace_compare_and_set_rejects_stale_revision(tmp_path: Path) -> None:
    repository = await _repository(tmp_path / "cloud.sqlite3")
    original = await _store_workspace(repository, _workspace())
    busy = transition_workspace(
        original,
        WorkspaceState.BUSY,
        at=NOW + timedelta(seconds=1),
    )
    stored = await repository.update_workspace(
        busy,
        expected_revision=original.revision,
        expected_state=WorkspaceState.READY,
    )
    stale_release = transition_workspace(
        original,
        WorkspaceState.RELEASING,
        at=NOW + timedelta(seconds=2),
    )

    with pytest.raises(CloudError) as raised:
        await repository.update_workspace(
            stale_release,
            expected_revision=original.revision,
            expected_state=WorkspaceState.READY,
        )

    assert stored.state is WorkspaceState.BUSY
    assert raised.value.code is CloudErrorCode.REVISION_CONFLICT
    await repository.close()


@pytest.mark.asyncio
async def test_workspace_release_transition_persists(tmp_path: Path) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    repository = await _repository(database_path)
    original = await _store_workspace(repository, _workspace())
    releasing = transition_workspace(
        original,
        WorkspaceState.RELEASING,
        at=NOW + timedelta(seconds=1),
    )
    stored_releasing = await repository.update_workspace(
        releasing,
        expected_revision=original.revision,
        expected_state=WorkspaceState.READY,
    )
    released = transition_workspace(
        stored_releasing,
        WorkspaceState.RELEASED,
        at=NOW + timedelta(seconds=2),
    )
    await repository.update_workspace(
        released,
        expected_revision=stored_releasing.revision,
        expected_state=WorkspaceState.RELEASING,
    )
    await repository.close()

    restarted = await _repository(database_path)
    restored = await restarted.get_workspace(original.id)
    assert restored == released
    assert restored is not None
    assert restored.released_at == NOW + timedelta(seconds=2)
    with sqlite3.connect(database_path) as raw_connection:
        serialized = raw_connection.execute(
            "SELECT created_at FROM workspaces WHERE id = ?", (str(original.id),)
        ).fetchone()[0]
    assert serialized.endswith("+00:00")
    await restarted.close()


@pytest.mark.asyncio
async def test_run_pagination_filter_tokens_and_cas_updates(tmp_path: Path) -> None:
    repository = await _repository(tmp_path / "cloud.sqlite3")
    await _store_workspace(repository, _workspace())
    for index in range(3):
        await _store_run(
            repository,
            _run(f"run-{index}", created_at=NOW + timedelta(seconds=index)),
        )

    first_page = await repository.list_runs(
        limit=2,
        workspace_id=WorkspaceId("workspace-1"),
        state=RunState.QUEUED,
    )
    assert [str(run.id) for run in first_page.items] == ["run-0", "run-1"]
    assert first_page.next_page_token is not None
    second_page = await repository.list_runs(
        limit=2,
        page_token=first_page.next_page_token,
        workspace_id=WorkspaceId("workspace-1"),
        state=RunState.QUEUED,
    )
    assert [str(run.id) for run in second_page.items] == ["run-2"]
    with pytest.raises(CloudError) as raised:
        await repository.list_runs(
            limit=2,
            page_token=first_page.next_page_token,
            state=RunState.QUEUED,
        )
    assert raised.value.code is CloudErrorCode.INVALID_REQUEST

    claimed = await repository.claim_run(
        worker_id="worker-1",
        lease_expires_at=NOW + timedelta(seconds=10),
    )
    assert claimed is not None
    running = transition_run(
        claimed,
        RunState.RUNNING,
        at=NOW + timedelta(seconds=1),
    )
    stored = await repository.update_run(
        running,
        expected_revision=claimed.revision,
        expected_state=RunState.STARTING,
    )
    with pytest.raises(CloudError) as stale:
        await repository.update_run(
            running,
            expected_revision=claimed.revision,
            expected_state=RunState.STARTING,
        )
    assert stored.state is RunState.RUNNING
    assert stale.value.code is CloudErrorCode.REVISION_CONFLICT
    await repository.close()


@pytest.mark.asyncio
async def test_heartbeat_expiry_cancellation_and_retry_are_durable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    repository = await _repository(database_path)
    await _store_workspace(repository, _workspace())
    await _store_run(repository, _run())
    claimed = await repository.claim_run(
        worker_id="worker-1",
        lease_expires_at=NOW + timedelta(seconds=5),
    )
    assert claimed is not None

    heartbeat = await repository.heartbeat_run(
        claimed.id,
        worker_id="worker-1",
        expected_revision=claimed.revision,
        lease_expires_at=NOW + timedelta(seconds=15),
    )
    assert heartbeat.revision == claimed.revision + 1
    assert (
        await repository.list_expired_runs(
            expired_before=NOW + timedelta(seconds=14), limit=10
        )
        == ()
    )
    assert [
        run.id
        for run in await repository.list_expired_runs(
            expired_before=NOW + timedelta(seconds=15), limit=10
        )
    ] == [claimed.id]

    cancelling = await repository.request_run_cancellation(
        claimed.id,
        requested_at=NOW + timedelta(seconds=2),
        idempotency_key="cancel-run-1",
        request_fingerprint="cancel-fingerprint",
    )
    repeated = await repository.request_run_cancellation(
        claimed.id,
        requested_at=NOW + timedelta(seconds=3),
        idempotency_key="cancel-run-1",
        request_fingerprint="cancel-fingerprint",
    )
    assert cancelling.state is RunState.CANCELLING
    assert repeated == cancelling

    failed = transition_run(
        cancelling,
        RunState.FAILED,
        at=NOW + timedelta(seconds=4),
        error_code="executor_failed",
    )
    stored_failed = await repository.update_run(
        failed,
        expected_revision=cancelling.revision,
        expected_state=RunState.CANCELLING,
    )
    retry = create_retry_run(
        stored_failed,
        run_id=RunId("run-retry"),
        created_at=NOW + timedelta(seconds=5),
    )
    stored_retry = await repository.create_retry_run(
        retry,
        idempotency_key="retry-run-1",
        request_fingerprint="retry-fingerprint",
    )
    repeated_retry = await repository.create_retry_run(
        replace(retry, id=RunId("run-not-created")),
        idempotency_key="retry-run-1",
        request_fingerprint="retry-fingerprint",
    )
    assert stored_retry.retry_of_run_id == stored_failed.id
    assert repeated_retry == stored_retry

    await repository.close()
    restarted = await _repository(database_path)
    assert await restarted.get_run(stored_failed.id) == stored_failed
    assert await restarted.get_run(stored_retry.id) == stored_retry
    await restarted.close()


@pytest.mark.asyncio
async def test_queued_cancellation_is_terminal_and_idempotency_conflicts(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path / "cloud.sqlite3")
    await _store_workspace(repository, _workspace())
    run = await _store_run(repository, _run())

    cancelled = await repository.request_run_cancellation(
        run.id,
        requested_at=NOW + timedelta(seconds=1),
        idempotency_key="cancel-key",
        request_fingerprint="cancel-request",
    )
    assert cancelled.state is RunState.CANCELLED
    assert cancelled.finished_at == NOW + timedelta(seconds=1)
    with pytest.raises(CloudError) as raised:
        await repository.request_run_cancellation(
            run.id,
            requested_at=NOW + timedelta(seconds=2),
            idempotency_key="cancel-key",
            request_fingerprint="changed-request",
        )
    assert raised.value.code is CloudErrorCode.IDEMPOTENCY_CONFLICT
    await repository.close()


@pytest.mark.asyncio
async def test_event_sequences_and_run_file_manifests_persist(tmp_path: Path) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    repository = await _repository(database_path)
    await _store_workspace(repository, _workspace())
    run = await _store_run(repository, _run())

    for index in range(4):
        event = await repository.append_event(
            run.id,
            event_type="unknown.codex.event",
            payload={"index": index, "nested": {"preserved": True}},
            created_at=NOW + timedelta(seconds=index),
        )
        assert event.sequence == index + 1
    page = await repository.list_events(run.id, after_sequence=1, limit=2)
    assert [event.sequence for event in page.items] == [2, 3]
    assert page.next_page_token is not None
    final_page = await repository.list_events(run.id, after_sequence=3, limit=2)
    assert [event.sequence for event in final_page.items] == [4]

    run_file = RunFile(
        id=FileId("file-1"),
        run_id=run.id,
        kind=RunFileKind.STDOUT,
        relative_path=PurePosixPath("stdout.jsonl"),
        size_bytes=123,
        sha256="a" * 64,
        created_at=NOW,
    )
    assert await repository.register_run_file(run_file) == run_file
    assert await repository.register_run_file(run_file) == run_file
    assert await repository.get_run_file(run_file.id) == run_file
    assert await repository.list_run_files(run.id) == (run_file,)

    trajectory_file = RunFile(
        id=FileId("file-trajectory"),
        run_id=run.id,
        kind=RunFileKind.TRAJECTORY,
        relative_path=PurePosixPath("trajectory.atif.json"),
        size_bytes=456,
        sha256="b" * 64,
        created_at=NOW,
        trajectory=TrajectoryManifest(
            format=TrajectoryFormat.ATIF,
            schema_version=ATIF_SCHEMA_VERSION,
            role=TrajectoryRole.CANONICAL,
        ),
    )
    await repository.register_run_file(trajectory_file)

    await repository.close()
    restarted = await _repository(database_path)
    restored_events = await restarted.list_events(run.id, limit=10)
    assert [event.sequence for event in restored_events.items] == [1, 2, 3, 4]
    assert await restarted.list_run_files(run.id) == (run_file, trajectory_file)
    await restarted.close()


@pytest.mark.asyncio
async def test_database_lock_is_bounded_and_repository_recovers(tmp_path: Path) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    repository = SQLiteCloudRepository(
        database_path,
        busy_timeout=timedelta(milliseconds=25),
    )
    await repository.initialize()
    lock_connection = sqlite3.connect(database_path, isolation_level=None)
    lock_connection.execute("PRAGMA journal_mode = WAL")
    lock_connection.execute("BEGIN IMMEDIATE")

    with pytest.raises(CloudError) as raised:
        await _store_workspace(repository, _workspace())
    assert raised.value.code is CloudErrorCode.REPOSITORY_UNAVAILABLE

    lock_connection.rollback()
    lock_connection.close()
    assert await _store_workspace(repository, _workspace()) == _workspace()
    await repository.close()
