from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from aworld.cloud.models import RunState, WorkspaceId
from aworld.cloud.sqlite_repository import SQLiteCloudRepository
from tests.cloud.test_sqlite_repository import (
    NOW,
    _run,
    _store_run,
    _store_workspace,
    _workspace,
)


def _run_async(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.asyncio
async def test_two_repositories_contend_for_one_run_atomically(tmp_path: Path) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    first = SQLiteCloudRepository(database_path)
    second = SQLiteCloudRepository(database_path)
    await first.initialize()
    await second.initialize()
    await _store_workspace(first, _workspace())
    await _store_run(first, _run())

    lease = NOW + timedelta(seconds=30)
    results = await asyncio.gather(
        asyncio.to_thread(
            _run_async,
            first.claim_run(worker_id="worker-1", lease_expires_at=lease),
        ),
        asyncio.to_thread(
            _run_async,
            second.claim_run(worker_id="worker-2", lease_expires_at=lease),
        ),
    )

    claimed = [run for run in results if run is not None]
    assert len(claimed) == 1
    assert claimed[0].state is RunState.STARTING
    assert claimed[0].worker_id in {"worker-1", "worker-2"}
    stored = await first.get_run(claimed[0].id)
    assert stored == claimed[0]
    await first.close()
    await second.close()


@pytest.mark.asyncio
async def test_two_repositories_resolve_concurrent_idempotent_creation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    first = SQLiteCloudRepository(database_path)
    second = SQLiteCloudRepository(database_path)
    await first.initialize()
    await second.initialize()
    candidates = (_workspace("workspace-a"), _workspace("workspace-b"))

    results = await asyncio.gather(
        asyncio.to_thread(
            _run_async,
            first.create_workspace(
                candidates[0],
                idempotency_key="shared-key",
                request_fingerprint="same-logical-request",
            ),
        ),
        asyncio.to_thread(
            _run_async,
            second.create_workspace(
                candidates[1],
                idempotency_key="shared-key",
                request_fingerprint="same-logical-request",
            ),
        ),
    )

    assert results[0] == results[1]
    stored = await first.list_workspaces(limit=10)
    assert stored.items == (results[0],)
    await first.close()
    await second.close()


@pytest.mark.asyncio
async def test_claiming_preserves_one_active_run_per_workspace(tmp_path: Path) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    first = SQLiteCloudRepository(database_path)
    second = SQLiteCloudRepository(database_path)
    await first.initialize()
    await second.initialize()
    await _store_workspace(first, _workspace())
    await _store_run(first, _run("run-1"))
    await _store_run(first, _run("run-2", created_at=NOW + timedelta(seconds=1)))

    initial = await first.claim_run(
        worker_id="worker-1",
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    blocked = await second.claim_run(
        worker_id="worker-2",
        lease_expires_at=NOW + timedelta(seconds=30),
    )

    assert initial is not None
    assert blocked is None
    queued = await first.list_runs(
        limit=10,
        workspace_id=WorkspaceId("workspace-1"),
        state=RunState.QUEUED,
    )
    assert [str(run.id) for run in queued.items] == ["run-2"]
    await first.close()
    await second.close()


@pytest.mark.asyncio
async def test_concurrent_event_appends_remain_monotonic(tmp_path: Path) -> None:
    database_path = tmp_path / "cloud.sqlite3"
    first = SQLiteCloudRepository(database_path)
    second = SQLiteCloudRepository(database_path)
    await first.initialize()
    await second.initialize()
    await _store_workspace(first, _workspace())
    run = await _store_run(first, _run())

    repositories = (first, second) * 5
    events = await asyncio.gather(
        *(
            asyncio.to_thread(
                _run_async,
                repository.append_event(
                    run.id,
                    event_type="progress",
                    payload={"index": index},
                    created_at=NOW + timedelta(milliseconds=index),
                ),
            )
            for index, repository in enumerate(repositories)
        )
    )

    assert sorted(event.sequence for event in events) == list(range(1, 11))
    stored = await first.list_events(run.id, limit=20)
    assert [event.sequence for event in stored.items] == list(range(1, 11))
    await first.close()
    await second.close()
