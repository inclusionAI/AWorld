"""Persistence-neutral async protocols for durable cloud metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from aworld.cloud.models import (
    Batch,
    BatchId,
    FileId,
    Run,
    RunEvent,
    RunFile,
    RunId,
    RunState,
    Workspace,
    WorkspaceId,
    WorkspaceState,
)

RecordT = TypeVar("RecordT")


@dataclass(frozen=True)
class Page(Generic[RecordT]):
    """An opaque-token page returned by repository list operations."""

    items: tuple[RecordT, ...]
    next_page_token: str | None = None


@runtime_checkable
class WorkspaceRepository(Protocol):
    async def create_workspace(
        self,
        workspace: Workspace,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Workspace: ...

    async def get_workspace(self, workspace_id: WorkspaceId) -> Workspace | None: ...

    async def list_workspaces(
        self,
        *,
        limit: int,
        page_token: str | None = None,
    ) -> Page[Workspace]: ...

    async def update_workspace(
        self,
        workspace: Workspace,
        *,
        expected_revision: int,
        expected_state: WorkspaceState,
    ) -> Workspace: ...

    async def begin_workspace_release(
        self,
        workspace: Workspace,
        *,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Workspace: ...


@runtime_checkable
class RunRepository(Protocol):
    async def create_run(
        self,
        run: Run,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Run: ...

    async def get_run(self, run_id: RunId) -> Run | None: ...

    async def list_runs(
        self,
        *,
        limit: int,
        page_token: str | None = None,
        workspace_id: WorkspaceId | None = None,
        state: RunState | None = None,
        batch_id: BatchId | None = None,
    ) -> Page[Run]: ...

    async def claim_run(
        self,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> Run | None: ...

    async def update_run(
        self,
        run: Run,
        *,
        expected_revision: int,
        expected_state: RunState,
    ) -> Run: ...

    async def heartbeat_run(
        self,
        run_id: RunId,
        *,
        worker_id: str,
        expected_revision: int,
        lease_expires_at: datetime,
    ) -> Run: ...

    async def request_run_cancellation(
        self,
        run_id: RunId,
        *,
        requested_at: datetime,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Run: ...

    async def create_retry_run(
        self,
        run: Run,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Run: ...

    async def list_expired_runs(
        self,
        *,
        expired_before: datetime,
        limit: int,
    ) -> tuple[Run, ...]: ...


@runtime_checkable
class BatchRepository(Protocol):
    async def create_batch(
        self,
        batch: Batch,
        runs: tuple[Run, ...],
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Batch: ...

    async def get_batch(self, batch_id: BatchId) -> Batch | None: ...

    async def list_batches(
        self,
        *,
        limit: int,
        page_token: str | None = None,
        workspace_id: WorkspaceId | None = None,
    ) -> Page[Batch]: ...

    async def list_batch_runs(
        self,
        batch_id: BatchId,
        *,
        limit: int,
        page_token: str | None = None,
    ) -> Page[Run]: ...

    async def cancel_batch(
        self,
        batch_id: BatchId,
        *,
        requested_at: datetime,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Batch: ...


@runtime_checkable
class EventRepository(Protocol):
    async def append_event(
        self,
        run_id: RunId,
        *,
        event_type: str,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> RunEvent: ...

    async def list_events(
        self,
        run_id: RunId,
        *,
        after_sequence: int = 0,
        limit: int,
    ) -> Page[RunEvent]: ...


@runtime_checkable
class RunFileRepository(Protocol):
    async def register_run_file(self, run_file: RunFile) -> RunFile: ...

    async def get_run_file(self, file_id: FileId) -> RunFile | None: ...

    async def list_run_files(self, run_id: RunId) -> tuple[RunFile, ...]: ...


@runtime_checkable
class CloudRepository(
    WorkspaceRepository,
    BatchRepository,
    RunRepository,
    EventRepository,
    RunFileRepository,
    Protocol,
):
    """Complete storage contract used by the cloud service and worker."""

    async def initialize(self) -> None: ...

    async def close(self) -> None: ...
