"""Typed HTTP contracts for the versioned AWorld Cloud API."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

from aworld.cloud.errors import CloudErrorCode
from aworld.cloud.models import (
    MountAccessMode,
    Run,
    RunEvent,
    RunFile,
    RunFileKind,
    RunState,
    WorkspaceState,
    as_utc,
    format_utc_timestamp,
)
from aworld.cloud.service import WorkspaceInspection


def _serialize_timestamp(value: datetime) -> str:
    return format_utc_timestamp(value)


def _mutable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mutable_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mutable_json(item) for item in value]
    return value


UtcTimestamp = Annotated[
    datetime,
    AfterValidator(as_utc),
    PlainSerializer(_serialize_timestamp, return_type=str, when_used="json"),
]


class CloudApiModel(BaseModel):
    """Strict base model shared by cloud requests and responses."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class IdempotencyRequest(CloudApiModel):
    idempotency_key: str = Field(min_length=1, max_length=256)


class WorkspaceCreateRequest(IdempotencyRequest):
    name: str = Field(min_length=1, max_length=256)
    profile_name: str = Field(min_length=1, max_length=128)


class WorkspaceReleaseRequest(IdempotencyRequest):
    pass


class RunSubmitRequest(IdempotencyRequest):
    task: str = Field(min_length=1, max_length=1_000_000)
    model: str | None = Field(default=None, min_length=1, max_length=256)


class RunCancelRequest(IdempotencyRequest):
    pass


class RunRetryRequest(IdempotencyRequest):
    pass


class WorkspaceMountResponse(CloudApiModel):
    container_path: str
    access_mode: MountAccessMode


class WorkspaceResponse(CloudApiModel):
    id: str
    name: str
    profile_name: str
    state: WorkspaceState
    revision: int
    runtime_image: str
    workdir: str
    mounts: tuple[WorkspaceMountResponse, ...]
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    released_at: UtcTimestamp | None
    active_run_id: str | None
    codex_config_present: bool
    codex_auth_present: bool

    @classmethod
    def from_domain(cls, inspection: WorkspaceInspection) -> WorkspaceResponse:
        workspace = inspection.workspace
        return cls(
            id=str(workspace.id),
            name=workspace.name,
            profile_name=workspace.profile_name,
            state=workspace.state,
            revision=workspace.revision,
            runtime_image=workspace.runtime_image,
            workdir=str(workspace.workdir),
            mounts=tuple(
                WorkspaceMountResponse(
                    container_path=str(mount.container_path),
                    access_mode=mount.access_mode,
                )
                for mount in workspace.mounts
            ),
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
            released_at=workspace.released_at,
            active_run_id=(
                str(inspection.active_run_id)
                if inspection.active_run_id is not None
                else None
            ),
            codex_config_present=inspection.codex_config_present,
            codex_auth_present=inspection.codex_auth_present,
        )


class WorkspacePageResponse(CloudApiModel):
    items: tuple[WorkspaceResponse, ...]
    next_page_token: str | None = None


class RunResponse(CloudApiModel):
    id: str
    workspace_id: str
    state: RunState
    revision: int
    attempt: int
    retry_of_run_id: str | None
    task: str
    model: str | None
    worker_id: str | None
    executor_id: str | None
    created_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    duration_seconds: float | None
    exit_code: int | None
    error_code: str | None
    error_message: str | None
    file_count: int
    artifact_count: int

    @classmethod
    def from_domain(
        cls,
        run: Run,
        files: tuple[RunFile, ...] = (),
    ) -> RunResponse:
        duration = None
        if run.finished_at is not None:
            duration = (
                run.finished_at - (run.started_at or run.created_at)
            ).total_seconds()
        return cls(
            id=str(run.id),
            workspace_id=str(run.workspace_id),
            state=run.state,
            revision=run.revision,
            attempt=run.attempt,
            retry_of_run_id=(
                str(run.retry_of_run_id) if run.retry_of_run_id is not None else None
            ),
            task=run.task,
            model=run.model,
            worker_id=run.worker_id,
            executor_id=str(run.executor_id) if run.executor_id is not None else None,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_seconds=duration,
            exit_code=run.exit_code,
            error_code=run.error_code,
            error_message=run.error_message,
            file_count=len(files),
            artifact_count=sum(
                run_file.kind is RunFileKind.ARTIFACT for run_file in files
            ),
        )


class RunPageResponse(CloudApiModel):
    items: tuple[RunResponse, ...]
    next_page_token: str | None = None


class RunEventResponse(CloudApiModel):
    id: str
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: UtcTimestamp

    @classmethod
    def from_domain(cls, event: RunEvent) -> RunEventResponse:
        return cls(
            id=str(event.id),
            run_id=str(event.run_id),
            sequence=event.sequence,
            event_type=event.event_type,
            payload=_mutable_json(event.payload),
            created_at=event.created_at,
        )


class RunEventPageResponse(CloudApiModel):
    items: tuple[RunEventResponse, ...]
    next_after_sequence: int | None = None


class RunFileResponse(CloudApiModel):
    id: str
    run_id: str
    kind: RunFileKind
    relative_path: str
    size_bytes: int
    sha256: str
    created_at: UtcTimestamp
    download_url: str

    @classmethod
    def from_domain(cls, run_file: RunFile) -> RunFileResponse:
        return cls(
            id=str(run_file.id),
            run_id=str(run_file.run_id),
            kind=run_file.kind,
            relative_path=str(run_file.relative_path),
            size_bytes=run_file.size_bytes,
            sha256=run_file.sha256,
            created_at=run_file.created_at,
            download_url=(f"/api/v1/cloud/runs/{run_file.run_id}/files/{run_file.id}"),
        )


class RunFileListResponse(CloudApiModel):
    items: tuple[RunFileResponse, ...]


class CloudErrorBody(CloudApiModel):
    code: CloudErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class CloudErrorResponse(CloudApiModel):
    error: CloudErrorBody
