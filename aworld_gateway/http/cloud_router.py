"""Opt-in FastAPI routes for durable AWorld Cloud resources."""

from __future__ import annotations

import asyncio
import mimetypes
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from aworld.cloud.errors import CloudError, CloudErrorCode
from aworld.cloud.models import (
    TERMINAL_RUN_STATES,
    FileId,
    Run,
    RunFile,
    RunId,
    RunState,
    WorkspaceId,
)
from aworld.cloud.paths import CloudPaths
from aworld.cloud.repository import CloudRepository
from aworld.cloud.service import CloudService
from aworld.cloud.settings import CloudSettings
from aworld_gateway.http.cloud_models import (
    CloudErrorBody,
    CloudErrorResponse,
    RunCancelRequest,
    RunEventPageResponse,
    RunEventResponse,
    RunFileListResponse,
    RunFileResponse,
    RunPageResponse,
    RunResponse,
    RunRetryRequest,
    RunSubmitRequest,
    WorkspaceCreateRequest,
    WorkspacePageResponse,
    WorkspaceReleaseRequest,
    WorkspaceResponse,
)

_CLOUD_PREFIX = "/api/v1/cloud"
_RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)\Z")
_FILE_CHUNK_SIZE = 64 * 1024
_HTTP_UNPROCESSABLE_CONTENT = 422
_HTTP_RANGE_NOT_SATISFIABLE = 416


@dataclass(frozen=True)
class CloudApiDependencies:
    """Explicit cloud dependencies injected by the gateway composition root."""

    service: CloudService
    repository: CloudRepository
    settings: CloudSettings


_ERROR_STATUSES = {
    CloudErrorCode.WORKSPACE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    CloudErrorCode.RUN_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    CloudErrorCode.FILE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    CloudErrorCode.WORKSPACE_BUSY: status.HTTP_409_CONFLICT,
    CloudErrorCode.INVALID_TRANSITION: status.HTTP_409_CONFLICT,
    CloudErrorCode.REVISION_CONFLICT: status.HTTP_409_CONFLICT,
    CloudErrorCode.IDEMPOTENCY_CONFLICT: status.HTTP_409_CONFLICT,
    CloudErrorCode.REPOSITORY_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    CloudErrorCode.EXECUTOR_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    CloudErrorCode.WORKSPACE_PROVISION_FAILED: status.HTTP_500_INTERNAL_SERVER_ERROR,
    CloudErrorCode.EXECUTOR_FAILED: status.HTTP_500_INTERNAL_SERVER_ERROR,
    CloudErrorCode.WORKER_LEASE_EXPIRED: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": CloudErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": CloudErrorResponse},
    status.HTTP_409_CONFLICT: {"model": CloudErrorResponse},
    _HTTP_UNPROCESSABLE_CONTENT: {"model": CloudErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": CloudErrorResponse},
}


def _error_payload(
    code: CloudErrorCode,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    return CloudErrorResponse(
        error=CloudErrorBody(
            code=code,
            message=message,
            details=details or {},
        )
    ).model_dump(mode="json")


async def _cloud_error_handler(request: Request, exc: Exception) -> JSONResponse:
    del request
    if not isinstance(exc, CloudError):
        raise exc
    return JSONResponse(
        status_code=_ERROR_STATUSES.get(exc.code, status.HTTP_400_BAD_REQUEST),
        content=_error_payload(
            exc.code,
            exc.message,
            details=dict(exc.details),
        ),
    )


async def _request_validation_handler(
    request: Request,
    exc: Exception,
) -> Response:
    if not request.url.path.startswith(_CLOUD_PREFIX):
        if not isinstance(exc, RequestValidationError):
            raise exc
        return await request_validation_exception_handler(request, exc)
    if not isinstance(exc, RequestValidationError):
        raise exc
    errors = [
        {
            "location": ".".join(str(part) for part in error["loc"]),
            "message": str(error["msg"]),
            "type": str(error["type"]),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=_HTTP_UNPROCESSABLE_CONTENT,
        content=_error_payload(
            CloudErrorCode.INVALID_REQUEST,
            "request validation failed",
            details={"errors": errors},
        ),
    )


async def _run_response(
    repository: CloudRepository,
    run: Run,
) -> RunResponse:
    # The service always returns a Run; keeping repository access here makes the
    # artifact summary durable without adding transport concerns to the service.
    files = await repository.list_run_files(run.id)
    return RunResponse.from_domain(run, files)


def _parse_last_event_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        sequence = int(value)
    except ValueError as exc:
        raise CloudError(
            CloudErrorCode.INVALID_REQUEST,
            "Last-Event-ID must be a non-negative event sequence",
        ) from exc
    if sequence < 0:
        raise CloudError(
            CloudErrorCode.INVALID_REQUEST,
            "Last-Event-ID must be a non-negative event sequence",
        )
    return sequence


def _format_sse(event: RunEventResponse) -> bytes:
    data = event.model_dump_json()
    return (
        f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"
    ).encode()


def _manifest_path(paths: CloudPaths, run_file: RunFile) -> Path:
    run_root = paths.run_output(run_file.run_id).resolve(strict=False)
    try:
        candidate = (run_root / run_file.relative_path).resolve(strict=True)
        stat_result = candidate.stat()
    except (FileNotFoundError, OSError) as exc:
        raise CloudError(
            CloudErrorCode.FILE_NOT_FOUND,
            "run file is unavailable",
        ) from exc
    if (
        not candidate.is_relative_to(run_root)
        or not candidate.is_file()
        or stat_result.st_size != run_file.size_bytes
    ):
        raise CloudError(
            CloudErrorCode.FILE_NOT_FOUND,
            "run file is unavailable",
        )
    return candidate


def _parse_range(value: str, size: int) -> tuple[int, int]:
    match = _RANGE_PATTERN.fullmatch(value.strip())
    if match is None or size == 0:
        raise ValueError("invalid byte range")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise ValueError("invalid byte range")
    if not start_text:
        suffix = int(end_text)
        if suffix <= 0:
            raise ValueError("invalid byte range")
        start = max(0, size - suffix)
        return start, size - 1
    start = int(start_text)
    if start >= size:
        raise ValueError("invalid byte range")
    end = size - 1 if not end_text else min(int(end_text), size - 1)
    if end < start:
        raise ValueError("invalid byte range")
    return start, end


def _file_chunks(path: Path, *, offset: int, length: int) -> Iterator[bytes]:
    with path.open("rb") as stream:
        stream.seek(offset)
        remaining = length
        while remaining:
            chunk = stream.read(min(_FILE_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _range_error(size: int) -> JSONResponse:
    return JSONResponse(
        status_code=_HTTP_RANGE_NOT_SATISFIABLE,
        content=_error_payload(
            CloudErrorCode.INVALID_REQUEST,
            "requested byte range is not satisfiable",
        ),
        headers={"content-range": f"bytes */{size}"},
    )


def create_cloud_router(dependencies: CloudApiDependencies) -> APIRouter:
    """Build the versioned router without initializing global state."""

    service = dependencies.service
    repository = dependencies.repository
    settings = dependencies.settings
    paths = CloudPaths(settings)
    router = APIRouter(prefix=_CLOUD_PREFIX, tags=["cloud"])

    @router.post(
        "/workspaces",
        response_model=WorkspaceResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_ERROR_RESPONSES,
    )
    async def create_workspace(payload: WorkspaceCreateRequest) -> WorkspaceResponse:
        inspection = await service.create_workspace(
            name=payload.name,
            profile_name=payload.profile_name,
            idempotency_key=payload.idempotency_key,
        )
        return WorkspaceResponse.from_domain(inspection)

    @router.get(
        "/workspaces",
        response_model=WorkspacePageResponse,
        responses=_ERROR_RESPONSES,
    )
    async def list_workspaces(
        limit: int = Query(default=50, ge=1, le=1000),
        page_token: str | None = Query(default=None, max_length=4096),
    ) -> WorkspacePageResponse:
        page = await service.list_workspaces(limit=limit, page_token=page_token)
        return WorkspacePageResponse(
            items=tuple(WorkspaceResponse.from_domain(item) for item in page.items),
            next_page_token=page.next_page_token,
        )

    @router.get(
        "/workspaces/{workspace_id}",
        response_model=WorkspaceResponse,
        responses=_ERROR_RESPONSES,
    )
    async def get_workspace(workspace_id: str) -> WorkspaceResponse:
        inspection = await service.get_workspace(WorkspaceId(workspace_id))
        return WorkspaceResponse.from_domain(inspection)

    @router.delete(
        "/workspaces/{workspace_id}",
        response_model=WorkspaceResponse,
        responses=_ERROR_RESPONSES,
    )
    async def release_workspace(
        workspace_id: str,
        payload: WorkspaceReleaseRequest,
    ) -> WorkspaceResponse:
        inspection = await service.release_workspace(
            WorkspaceId(workspace_id),
            idempotency_key=payload.idempotency_key,
        )
        return WorkspaceResponse.from_domain(inspection)

    @router.post(
        "/workspaces/{workspace_id}/runs",
        response_model=RunResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_ERROR_RESPONSES,
    )
    async def submit_run(workspace_id: str, payload: RunSubmitRequest) -> RunResponse:
        run = await service.submit_run(
            WorkspaceId(workspace_id),
            task=payload.task,
            model=payload.model,
            idempotency_key=payload.idempotency_key,
        )
        return await _run_response(repository, run)

    @router.get(
        "/runs",
        response_model=RunPageResponse,
        responses=_ERROR_RESPONSES,
    )
    async def list_runs(
        limit: int = Query(default=50, ge=1, le=1000),
        page_token: str | None = Query(default=None, max_length=4096),
        workspace_id: str | None = Query(default=None),
        state: Annotated[RunState | None, Query()] = None,
    ) -> RunPageResponse:
        page = await service.list_runs(
            limit=limit,
            page_token=page_token,
            workspace_id=(
                WorkspaceId(workspace_id) if workspace_id is not None else None
            ),
            state=state,
        )
        items = tuple([await _run_response(repository, run) for run in page.items])
        return RunPageResponse(items=items, next_page_token=page.next_page_token)

    @router.get(
        "/runs/{run_id}",
        response_model=RunResponse,
        responses=_ERROR_RESPONSES,
    )
    async def get_run(run_id: str) -> RunResponse:
        return await _run_response(repository, await service.get_run(RunId(run_id)))

    @router.post(
        "/runs/{run_id}/cancel",
        response_model=RunResponse,
        responses=_ERROR_RESPONSES,
    )
    async def cancel_run(run_id: str, payload: RunCancelRequest) -> RunResponse:
        run = await service.cancel_run(
            RunId(run_id),
            idempotency_key=payload.idempotency_key,
        )
        return await _run_response(repository, run)

    @router.post(
        "/runs/{run_id}/retry",
        response_model=RunResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_ERROR_RESPONSES,
    )
    async def retry_run(run_id: str, payload: RunRetryRequest) -> RunResponse:
        run = await service.retry_run(
            RunId(run_id),
            idempotency_key=payload.idempotency_key,
        )
        return await _run_response(repository, run)

    @router.get(
        "/runs/{run_id}/events",
        response_model=RunEventPageResponse,
        responses=_ERROR_RESPONSES,
    )
    async def list_events(
        run_id: str,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> RunEventPageResponse:
        page = await repository.list_events(
            RunId(run_id),
            after_sequence=after_sequence,
            limit=limit,
        )
        items = tuple(RunEventResponse.from_domain(event) for event in page.items)
        return RunEventPageResponse(
            items=items,
            next_after_sequence=(
                items[-1].sequence
                if page.next_page_token is not None and items
                else None
            ),
        )

    @router.get(
        "/runs/{run_id}/events/stream",
        response_model=None,
        responses=_ERROR_RESPONSES,
    )
    async def stream_events(
        request: Request,
        run_id: str,
        after_sequence: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        typed_run_id = RunId(run_id)
        await service.get_run(typed_run_id)
        header_sequence = _parse_last_event_id(last_event_id)
        cursor = max(after_sequence, header_sequence or 0)

        async def event_stream() -> AsyncIterator[bytes]:
            nonlocal cursor
            terminal_empty_polls = 0
            while not await request.is_disconnected():
                page = await repository.list_events(
                    typed_run_id,
                    after_sequence=cursor,
                    limit=100,
                )
                if page.items:
                    terminal_empty_polls = 0
                    for event in page.items:
                        cursor = event.sequence
                        yield _format_sse(RunEventResponse.from_domain(event))
                    continue
                run = await service.get_run(typed_run_id)
                if run.state in TERMINAL_RUN_STATES:
                    terminal_empty_polls += 1
                    if terminal_empty_polls >= 2:
                        break
                await asyncio.sleep(max(0.01, settings.poll_interval.total_seconds()))

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache",
                "x-accel-buffering": "no",
            },
        )

    @router.get(
        "/runs/{run_id}/files",
        response_model=RunFileListResponse,
        responses=_ERROR_RESPONSES,
    )
    async def list_files(run_id: str) -> RunFileListResponse:
        typed_run_id = RunId(run_id)
        await service.get_run(typed_run_id)
        files = await repository.list_run_files(typed_run_id)
        return RunFileListResponse(
            items=tuple(RunFileResponse.from_domain(run_file) for run_file in files)
        )

    @router.get(
        "/runs/{run_id}/files/{file_id}",
        response_model=None,
        responses={
            **_ERROR_RESPONSES,
            _HTTP_RANGE_NOT_SATISFIABLE: {"model": CloudErrorResponse},
        },
    )
    async def get_file(
        run_id: str,
        file_id: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> Response:
        typed_run_id = RunId(run_id)
        await service.get_run(typed_run_id)
        run_file = await repository.get_run_file(FileId(file_id))
        if run_file is None or run_file.run_id != typed_run_id:
            raise CloudError(
                CloudErrorCode.FILE_NOT_FOUND,
                "run file does not exist",
            )
        path = _manifest_path(paths, run_file)
        start = 0
        end = run_file.size_bytes - 1
        response_status = status.HTTP_200_OK
        if range_header is not None:
            try:
                start, end = _parse_range(range_header, run_file.size_bytes)
            except (TypeError, ValueError):
                return _range_error(run_file.size_bytes)
            response_status = status.HTTP_206_PARTIAL_CONTENT
        length = 0 if run_file.size_bytes == 0 else end - start + 1
        headers = {
            "accept-ranges": "bytes",
            "content-length": str(length),
            "content-disposition": (
                f"attachment; filename*=UTF-8''{quote(path.name, safe='')}"
            ),
        }
        if response_status == status.HTTP_206_PARTIAL_CONTENT:
            headers["content-range"] = f"bytes {start}-{end}/{run_file.size_bytes}"
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return StreamingResponse(
            _file_chunks(path, offset=start, length=length),
            status_code=response_status,
            media_type=media_type,
            headers=headers,
        )

    return router


def register_cloud_routes(
    app: FastAPI,
    dependencies: CloudApiDependencies | None,
) -> bool:
    """Register cloud routes only for an explicitly enabled dependency bundle."""

    if dependencies is None or not dependencies.settings.enabled:
        return False
    app.add_exception_handler(CloudError, _cloud_error_handler)
    app.add_exception_handler(RequestValidationError, _request_validation_handler)
    app.include_router(create_cloud_router(dependencies))
    return True
