"""Small asynchronous HTTP boundary for the FileX CLI.

The server deliberately keeps parsing in a child process.  That preserves the
CLI contract, isolates provider crashes, and lets the HTTP process remain
responsive while a large document is parsed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import signal
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

import aiohttp
from aiohttp import web

from .paths import FS_WORKSPACE_ROOT
from .pdf.pdf_batch_checkpoint import PdfBatchCheckpointStore

logger = logging.getLogger(__name__)
_PADDLE_RESPONSE_PREFIX = b"FILEX_PADDLE_RESPONSE\t"

_PAGE_SELECTION_RE = re.compile(r"^[1-9]\d*(?:-[1-9]\d*)?(?:,[1-9]\d*(?:-[1-9]\d*)?)*$")
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
_PUBLIC_SOURCE_TYPES = {"pdf", "mp4", "mov", "m4v", "mkv", "webm", "avi", "mpeg", "mpg"}
_ARTIFACT_FIELDS = {
    "markdown": "file_path",
    "metrics": "metrics_file_path",
    "evidence": "evidence_file_path",
    "document": "document_file_path",
    "storyboard": "storyboard_file_path",
}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_filename(value: str) -> str:
    decoded = unquote(str(value or "document.bin")).replace("\\", "/")
    leaf = decoded.rsplit("/", 1)[-1]
    cleaned = "".join(character for character in leaf if character.isprintable())
    return cleaned[:255] or "document.bin"


def _service_token_from_environment() -> str:
    token_file = os.getenv("FILEX_SERVICE_API_TOKEN_FILE", "").strip()
    if token_file:
        return Path(token_file).read_text(encoding="utf-8").strip()
    return os.getenv("FILEX_SERVICE_API_TOKEN", "").strip()


def _source_url_hosts_from_environment() -> set[str]:
    return {
        item.strip().lower()
        for item in os.getenv("FILEX_SERVICE_SOURCE_URL_HOSTS", "").split(",")
        if item.strip()
    }


def _is_public_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


class _SafeSourceResolver(aiohttp.abc.AbstractResolver):
    """Resolve one source hop and reject every non-public address."""

    def __init__(self, *, allow_private: bool) -> None:
        self._allow_private = allow_private
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[dict[str, Any]]:
        addresses = await self._resolver.resolve(host, port, family)
        if not self._allow_private and (
            not addresses
            or any(
                not _is_public_ip(str(address.get("host") or ""))
                for address in addresses
            )
        ):
            raise OSError("source_url resolved to a non-public address")
        return addresses

    async def close(self) -> None:
        await self._resolver.close()


class FileXHttpService:
    """Persist job state and run FileX with bounded local concurrency."""

    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        max_upload_bytes: int | None = None,
        concurrency: int | None = None,
        max_pending_jobs: int | None = None,
        parse_timeout_seconds: int | None = None,
        paddle_no_progress_seconds: int | None = None,
        paddle_idle_seconds: int | None = None,
        paddle_warmup: bool | None = None,
        paddle_warmup_timeout_seconds: int | None = None,
        paddle_warmup_max_attempts: int | None = None,
        api_token: str | None = None,
        tenant_id: str | None = None,
        source_url_hosts: set[str] | None = None,
    ) -> None:
        self.workspace_root = (
            (workspace_root or FS_WORKSPACE_ROOT).expanduser().resolve()
        )
        self.service_root = self.workspace_root / "filex-service"
        self.jobs_root = self.service_root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = max_upload_bytes or _int_env(
            "FILEX_SERVICE_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024
        )
        self.concurrency = max(
            1, concurrency or _int_env("FILEX_SERVICE_CONCURRENCY", 1)
        )
        self.max_pending_jobs = max(
            self.concurrency,
            max_pending_jobs or _int_env("FILEX_SERVICE_MAX_PENDING_JOBS", 8),
        )
        self.parse_timeout_seconds = max(
            1,
            parse_timeout_seconds
            or _int_env("FILEX_SERVICE_PARSE_TIMEOUT_SECONDS", 1800),
        )
        self.paddle_no_progress_seconds = max(
            1,
            paddle_no_progress_seconds
            or _int_env("FILEX_SERVICE_PADDLE_NO_PROGRESS_SECONDS", 300),
        )
        self.paddle_idle_seconds = max(
            0,
            paddle_idle_seconds
            if paddle_idle_seconds is not None
            else _int_env("FILEX_SERVICE_PADDLE_IDLE_SECONDS", 0),
        )
        self.paddle_warmup = (
            paddle_warmup
            if paddle_warmup is not None
            else _bool_env("FILEX_SERVICE_PADDLE_WARMUP", False)
        )
        self.paddle_warmup_timeout_seconds = max(
            1,
            paddle_warmup_timeout_seconds
            or _int_env("FILEX_SERVICE_PADDLE_WARMUP_TIMEOUT_SECONDS", 60),
        )
        self.paddle_warmup_max_attempts = max(
            1,
            paddle_warmup_max_attempts
            or _int_env("FILEX_SERVICE_PADDLE_WARMUP_MAX_ATTEMPTS", 3),
        )
        self.api_token = (
            api_token if api_token is not None else _service_token_from_environment()
        )
        self.tenant_id = (
            tenant_id
            if tenant_id is not None
            else os.getenv("FILEX_SERVICE_TENANT_ID", "").strip()
        )
        self.source_url_hosts = (
            {item.strip().lower() for item in source_url_hosts if item.strip()}
            if source_url_hosts is not None
            else _source_url_hosts_from_environment()
        )
        self._semaphore = asyncio.Semaphore(self.concurrency)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._paddle_worker: asyncio.subprocess.Process | None = None
        self._paddle_worker_lock = asyncio.Lock()
        self._paddle_worker_state = "stopped"
        self._paddle_worker_started_at: float | None = None
        self._paddle_worker_last_used_at: float | None = None
        self._paddle_worker_log_task: asyncio.Task[None] | None = None
        self._paddle_worker_idle_task: asyncio.Task[None] | None = None
        self._paddle_warmup_task: asyncio.Task[None] | None = None
        self._paddle_warmup_completed = not self.paddle_warmup
        self._paddle_worker_warm = False
        self._recover_interrupted_jobs()

    def create_app(self) -> web.Application:
        app = web.Application(client_max_size=self.max_upload_bytes + 1024 * 1024)
        app["filex_service"] = self
        app.router.add_get("/healthz", self.handle_health)
        app.router.add_post("/v1/parse", self.handle_parse)
        app.router.add_delete("/v1/jobs/{job_id}", self.handle_cancel_job)
        app.router.add_get("/v1/jobs/{job_id}", self.handle_job)
        app.router.add_get("/v1/jobs/{job_id}/batches", self.handle_batches)
        app.router.add_get(
            "/v1/jobs/{job_id}/artifacts/{artifact_name}", self.handle_artifact
        )
        app.router.add_get(
            "/v1/jobs/{job_id}/artifacts/{artifact_path:.+}",
            self.handle_artifact_file,
        )
        app.on_startup.append(self._startup)
        app.on_cleanup.append(self._cleanup)
        return app

    async def handle_health(self, request: web.Request) -> web.Response:
        active_jobs = [
            self._read_job(job_id)
            for job_id, task in self._tasks.items()
            if not task.done()
        ]
        running = sum(job.get("status") == "running" for job in active_jobs)
        queued = sum(job.get("status") == "queued" for job in active_jobs)
        worker_alive = bool(
            self._paddle_worker and self._paddle_worker.returncode is None
        )
        ready = self._paddle_warmup_completed and self._paddle_worker_state != "failed"
        if self.paddle_warmup and self.paddle_idle_seconds == 0:
            ready = ready and worker_alive and self._paddle_worker_warm
        return web.json_response(
            {
                "status": "ok",
                "service": "filex",
                "active_jobs": len(active_jobs),
                "running_jobs": running,
                "queued_jobs": queued,
                "concurrency": self.concurrency,
                "max_pending_jobs": self.max_pending_jobs,
                "parse_timeout_seconds": self.parse_timeout_seconds,
                "paddle_no_progress_seconds": self.paddle_no_progress_seconds,
                "paddle_idle_seconds": self.paddle_idle_seconds,
                "paddle_warmup_timeout_seconds": self.paddle_warmup_timeout_seconds,
                "paddle_warmup_max_attempts": self.paddle_warmup_max_attempts,
                "paddle_worker_state": self._paddle_worker_state,
                "paddle_worker_pid": (
                    self._paddle_worker.pid
                    if self._paddle_worker and self._paddle_worker.returncode is None
                    else None
                ),
                "ready": ready,
            },
            status=200 if ready else 503,
        )

    async def handle_parse(self, request: web.Request) -> web.Response:
        self._authorize(request)
        active = sum(not task.done() for task in self._tasks.values())
        if active >= self.max_pending_jobs:
            raise web.HTTPTooManyRequests(
                text="FileX parse queue is full; retry later",
                headers={"Retry-After": "30"},
            )
        if not request.content_type.startswith("multipart/"):
            raise web.HTTPUnsupportedMediaType(text="multipart/form-data is required")

        job_id = f"filex-{uuid.uuid4().hex}"
        job_dir = self.jobs_root / job_id
        job_dir.mkdir(mode=0o750)
        upload_path = job_dir / "source.upload"
        filename = "document.bin"
        pages = ""
        pdf_provider = "liteparse"
        provider = ""
        asset_reference_mode = "local_path"
        force_refresh = False
        source_url = ""
        source_filename = ""
        source_sha256 = ""
        source_size = 0
        trusted_source_url = False
        size = 0
        digest = hashlib.sha256()
        found_file = False

        try:
            reader = await request.multipart()
            async for part in reader:
                if part.name == "file":
                    if found_file:
                        raise web.HTTPBadRequest(text="only one file is accepted")
                    found_file = True
                    filename = _safe_filename(part.filename or filename)
                    with upload_path.open("wb") as output:
                        while True:
                            chunk = await part.read_chunk(size=1024 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > self.max_upload_bytes:
                                raise web.HTTPRequestEntityTooLarge(
                                    max_size=self.max_upload_bytes,
                                    actual_size=size,
                                )
                            digest.update(chunk)
                            output.write(chunk)
                elif part.name == "pages":
                    pages = (await part.text()).strip()
                elif part.name == "pdf_provider":
                    pdf_provider = (await part.text()).strip().lower()
                elif part.name == "provider":
                    provider = (await part.text()).strip().lower()
                elif part.name == "asset_reference_mode":
                    asset_reference_mode = (await part.text()).strip()
                elif part.name == "force_refresh":
                    force_refresh = (await part.text()).strip().lower() in {
                        "1",
                        "true",
                        "yes",
                    }
                elif part.name == "source_url":
                    source_url = (await part.text()).strip()
                elif part.name == "source_filename":
                    source_filename = _safe_filename((await part.text()).strip())
                elif part.name == "source_sha256":
                    source_sha256 = (await part.text()).strip().lower()
                elif part.name == "source_size":
                    raw_size = (await part.text()).strip()
                    try:
                        source_size = int(raw_size) if raw_size else 0
                    except ValueError as exc:
                        raise web.HTTPBadRequest(text="invalid source_size") from exc

            if found_file == bool(source_url):
                raise web.HTTPBadRequest(
                    text="provide exactly one of file or source_url"
                )
            if found_file and size == 0:
                raise web.HTTPBadRequest(text="a non-empty file field is required")
            if source_url:
                trusted_source_url = self._validate_source_url(source_url)
                if trusted_source_url:
                    if not source_filename:
                        raise web.HTTPBadRequest(
                            text="source_filename is required for a trusted source_url"
                        )
                    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
                        raise web.HTTPBadRequest(
                            text="valid source_sha256 is required for a trusted source_url"
                        )
                    if source_size < 1 or source_size > self.max_upload_bytes:
                        raise web.HTTPBadRequest(text="invalid source_size")
                elif bool(source_sha256) != bool(source_size):
                    raise web.HTTPBadRequest(
                        text="source_sha256 and source_size must be provided together"
                    )
                elif source_sha256 and not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
                    raise web.HTTPBadRequest(text="invalid source_sha256")
                elif source_size and not 1 <= source_size <= self.max_upload_bytes:
                    raise web.HTTPBadRequest(text="invalid source_size")
                filename = source_filename or _safe_filename(urlsplit(source_url).path)
                public_suffix = Path(filename).suffix.lower().lstrip(".")
                if not trusted_source_url and public_suffix not in _PUBLIC_SOURCE_TYPES:
                    filename = f"{filename}.pdf"
            if pages and not _PAGE_SELECTION_RE.fullmatch(pages):
                raise web.HTTPBadRequest(text="invalid pages selection")
            if pdf_provider not in {"liteparse", "paddle_ocr"}:
                raise web.HTTPBadRequest(text="invalid pdf_provider")
            if provider and provider not in {"liteparse", "paddle_ocr"}:
                raise web.HTTPBadRequest(text="invalid provider")
            selected_provider = provider or pdf_provider
            if asset_reference_mode not in {"local_path", "remote_id"}:
                raise web.HTTPBadRequest(text="invalid asset_reference_mode")

            suffix = Path(filename).suffix.lower()[:16]
            source_path = job_dir / f"source{suffix}"
            if found_file:
                upload_path.replace(source_path)
            job = {
                "id": job_id,
                "tenant_id": request.headers.get("X-Tenant-ID") or None,
                "status": "queued",
                "queued_at": time.time(),
                "started_at": None,
                "completed_at": None,
                "filename": filename,
                "source_path": str(source_path.relative_to(self.workspace_root)),
                "source_sha256": source_sha256 if source_url else digest.hexdigest(),
                "source_size": source_size if source_url else size,
                "source_mode": (
                    "source_url"
                    if trusted_source_url
                    else "public_url"
                    if source_url
                    else "upload"
                ),
                "pages": pages or None,
                "provider": selected_provider,
                "pdf_provider": pdf_provider,
                "page_batch_size": 3 if selected_provider == "paddle_ocr" else None,
                "asset_reference_mode": asset_reference_mode,
                "force_refresh": force_refresh,
                "result": None,
                "error": None,
            }
            self._write_job(job)
            task = asyncio.create_task(
                self._run_job(job_id, source_url=source_url or None)
            )
            self._tasks[job_id] = task
            task.add_done_callback(
                lambda _task, completed_job_id=job_id: self._tasks.pop(
                    completed_job_id, None
                )
            )
            return web.json_response(self._public_job(job), status=202)
        except Exception:
            if upload_path.exists():
                upload_path.unlink()
            if not any(job_dir.iterdir()):
                job_dir.rmdir()
            raise

    def _validate_source_url(self, source_url: str) -> bool:
        """Validate URL syntax and return whether it is a trusted object URL."""

        if len(source_url) > 8192:
            raise web.HTTPBadRequest(text="source_url is too long")
        parsed = urlsplit(source_url)
        authority = parsed.netloc.lower()
        trusted = authority in self.source_url_hosts
        if not parsed.hostname or parsed.username or parsed.password:
            raise web.HTTPBadRequest(text="invalid source_url")
        if trusted:
            if parsed.scheme not in {"http", "https"}:
                raise web.HTTPBadRequest(text="invalid source_url scheme")
            return True
        if parsed.scheme != "https":
            raise web.HTTPBadRequest(text="public source_url must use HTTPS")
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".local"):
            raise web.HTTPBadRequest(text="public source_url host is not allowed")
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise web.HTTPBadRequest(text="public source_url host is not allowed")
        return False

    async def _download_source_url(
        self, source_url: str, target: Path, *, expected_file_type: str | None
    ) -> tuple[int, str]:
        timeout = aiohttp.ClientTimeout(
            total=max(1, _int_env("FILEX_SERVICE_SOURCE_URL_TIMEOUT_SECONDS", 900))
        )
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()
        current_url = source_url
        try:
            for redirect_count in range(4):
                trusted = self._validate_source_url(current_url)
                public_source = expected_file_type is not None
                if public_source and trusted:
                    raise web.HTTPBadRequest(
                        text="public source_url cannot redirect to a trusted host"
                    )
                resolver = _SafeSourceResolver(
                    allow_private=trusted and not public_source
                )
                connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
                async with (
                    aiohttp.ClientSession(
                        timeout=timeout,
                        connector=connector,
                        headers={"User-Agent": "FileX/1.0"},
                    ) as session,
                    session.get(current_url, allow_redirects=False) as response,
                ):
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location", "").strip()
                        if trusted:
                            raise web.HTTPBadRequest(
                                text="trusted source_url redirects are not allowed"
                            )
                        if not location or redirect_count == 3:
                            raise web.HTTPBadRequest(
                                text="source_url redirect limit exceeded"
                            )
                        current_url = urljoin(current_url, location)
                        self._validate_source_url(current_url)
                        continue
                    if response.status != 200:
                        raise web.HTTPBadRequest(
                            text=f"source_url returned HTTP {response.status}"
                        )
                    declared_size = response.content_length
                    if (
                        declared_size is not None
                        and declared_size > self.max_upload_bytes
                    ):
                        raise web.HTTPRequestEntityTooLarge(
                            max_size=self.max_upload_bytes,
                            actual_size=declared_size,
                        )
                    with target.open("wb") as output:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            size += len(chunk)
                            if size > self.max_upload_bytes:
                                raise web.HTTPRequestEntityTooLarge(
                                    max_size=self.max_upload_bytes,
                                    actual_size=size,
                                )
                            digest.update(chunk)
                            if len(prefix) < 16:
                                prefix.extend(chunk[: 16 - len(prefix)])
                            output.write(chunk)
                    break
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise web.HTTPBadRequest(text="source_url download failed") from exc
        if size == 0:
            raise web.HTTPBadRequest(text="source_url returned an empty file")
        if expected_file_type and not self._matches_public_source_signature(
            expected_file_type, bytes(prefix)
        ):
            target.unlink(missing_ok=True)
            raise web.HTTPBadRequest(
                text=f"public source_url did not return a valid {expected_file_type} file"
            )
        return size, digest.hexdigest()

    @staticmethod
    def _matches_public_source_signature(file_type: str, prefix: bytes) -> bool:
        normalized = file_type.lower().lstrip(".")
        if normalized == "pdf":
            return prefix.startswith(b"%PDF-")
        if normalized in {"mp4", "mov", "m4v"}:
            return len(prefix) >= 12 and prefix[4:8] == b"ftyp"
        if normalized in {"mkv", "webm"}:
            return prefix.startswith(b"\x1a\x45\xdf\xa3")
        if normalized == "avi":
            return prefix.startswith(b"RIFF") and prefix[8:12] == b"AVI "
        if normalized in {"mpeg", "mpg"}:
            return prefix.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"))
        return False

    async def handle_job(self, request: web.Request) -> web.Response:
        self._authorize(request)
        job = self._read_job(request.match_info["job_id"])
        return web.json_response(self._public_job(job))

    async def handle_cancel_job(self, request: web.Request) -> web.Response:
        """Cancel one queued/running job and release its worker slot."""

        self._authorize(request)
        job_id = request.match_info["job_id"]
        job = self._read_job(job_id)
        if job.get("status") in _TERMINAL_STATUSES:
            return web.json_response(self._public_job(job))
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        job = self._read_job(job_id)
        if job.get("status") not in _TERMINAL_STATUSES:
            job["status"] = "cancelled"
            job["error"] = "cancelled by caller"
            job["completed_at"] = time.time()
            self._write_job(job)
        return web.json_response(self._public_job(job))

    async def handle_batches(self, request: web.Request) -> web.Response:
        """Return completed PDF batches after the caller's durable cursor."""

        self._authorize(request)
        job = self._read_job(request.match_info["job_id"])
        try:
            after = max(int(request.query.get("after", "0")), 0)
            limit = min(max(int(request.query.get("limit", "10")), 1), 100)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="after and limit must be integers") from exc

        store = PdfBatchCheckpointStore(
            self.workspace_root / "document_parse" / "pdf_batch_checkpoints",
            str(job["id"]),
        )
        progress = store.read_progress()
        batches = store.read_incremental_results(
            after_batch_index=after,
            max_batches=limit,
        )
        cursor = max(
            [after, *(int(batch.get("batch_index") or 0) for batch in batches)]
        )
        status = str(progress.get("status") or job.get("status") or "queued")
        if job.get("status") in _TERMINAL_STATUSES:
            status = str(job["status"])
        return web.json_response(
            {
                "schema_version": "1.0",
                "stream_id": str(job["id"]),
                "cursor": cursor,
                "status": status,
                "is_final": status.lower() in _TERMINAL_STATUSES,
                "failed_batch": int(progress.get("failed_batch") or 0),
                "error": str(progress.get("error") or job.get("error") or ""),
                "batches": batches,
            }
        )

    async def handle_artifact(self, request: web.Request) -> web.StreamResponse:
        self._authorize(request)
        job = self._read_job(request.match_info["job_id"])
        if job.get("status") != "succeeded":
            raise web.HTTPConflict(text="job has not succeeded")
        artifact_name = request.match_info["artifact_name"]
        result_field = _ARTIFACT_FIELDS.get(artifact_name)
        if not result_field:
            raise web.HTTPNotFound(text="unknown artifact")
        relative_path = str((job.get("result") or {}).get(result_field) or "").strip()
        if not relative_path:
            raise web.HTTPNotFound(text="artifact was not produced")
        artifact_path = (self.workspace_root / relative_path).resolve()
        if (
            not artifact_path.is_relative_to(self.workspace_root)
            or not artifact_path.is_file()
        ):
            raise web.HTTPNotFound(text="artifact is unavailable")
        return web.FileResponse(artifact_path)

    async def handle_artifact_file(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Serve one nested file referenced by a completed Markdown artifact."""

        self._authorize(request)
        job = self._read_job(request.match_info["job_id"])
        if job.get("status") != "succeeded":
            raise web.HTTPConflict(text="job has not succeeded")
        relative_path = str(request.match_info.get("artifact_path") or "").strip()
        result_path = str((job.get("result") or {}).get("file_path") or "").strip()
        if not relative_path or not result_path:
            raise web.HTTPNotFound(text="artifact is unavailable")
        output_root = (self.workspace_root / result_path).resolve().parent
        artifact_path = (output_root / relative_path).resolve()
        if (
            not artifact_path.is_relative_to(output_root)
            or not artifact_path.is_file()
        ):
            raise web.HTTPNotFound(text="artifact is unavailable")
        return web.FileResponse(artifact_path)

    def _authorize(self, request: web.Request) -> None:
        if (
            self.tenant_id
            and request.headers.get("X-Tenant-ID", "") != self.tenant_id
        ):
            raise web.HTTPForbidden(text="invalid tenant")
        if self.api_token:
            supplied = request.headers.get("Authorization", "")
            if supplied != f"Bearer {self.api_token}":
                raise web.HTTPUnauthorized(text="invalid service token")

    async def _run_job(self, job_id: str, *, source_url: str | None = None) -> None:
        async with self._semaphore:
            job = self._read_job(job_id)
            job["status"] = "running"
            job["started_at"] = time.time()
            self._write_job(job)
            try:
                if source_url:
                    source_path = self.workspace_root / str(job["source_path"])
                    fetch_started = time.monotonic()
                    size, downloaded_sha256 = await self._download_source_url(
                        source_url,
                        source_path,
                        expected_file_type=(
                            Path(str(job["filename"])).suffix.lower().lstrip(".")
                            if job.get("source_mode") == "public_url"
                            else None
                        ),
                    )
                    if job.get("source_size") and size != int(job["source_size"]):
                        raise ValueError("downloaded source size mismatch")
                    if job.get("source_sha256") and downloaded_sha256 != str(
                        job["source_sha256"]
                    ):
                        raise ValueError("downloaded source sha256 mismatch")
                    job["source_size"] = size
                    job["source_sha256"] = downloaded_sha256
                    job["source_fetch_ms"] = round(
                        (time.monotonic() - fetch_started) * 1000,
                        2,
                    )
                    self._write_job(job)
                result, stderr, returncode = await self._execute_parse(job)
                job["result"] = result
                job["stderr_tail"] = stderr[-16_384:]
                if returncode == 0 and result.get("success"):
                    job["status"] = "succeeded"
                else:
                    job["status"] = "failed"
                    job["error"] = str(
                        result.get("error")
                        or result.get("message")
                        or stderr[-2_000:]
                        or f"filex exited with {returncode}"
                    )
            except asyncio.CancelledError:
                job["status"] = "cancelled"
                job["error"] = "cancelled by caller"
                raise
            except Exception as exc:
                logger.exception("FileX job failed | job_id=%s", job_id)
                job["status"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                job["completed_at"] = time.time()
                self._write_job(job)

    async def _execute_parse(
        self, job: dict[str, Any]
    ) -> tuple[dict[str, Any], str, int]:
        if (job.get("provider") or job.get("pdf_provider")) == "paddle_ocr":
            return await self._execute_persistent_paddle(job)
        source_path = self.workspace_root / str(job["source_path"])
        command = [
            "filex",
            "parse",
            "--workspace-path",
            str(source_path),
            "--sync-mode",
            "sync",
            "--asset-reference-mode",
            str(job["asset_reference_mode"]),
            "--task-id",
            str(job["id"]),
            "--batch-resume-id",
            str(job["id"]),
        ]
        if job.get("pages"):
            command.extend(["--pages", str(job["pages"])])
        if job.get("page_batch_size"):
            command.extend(["--page-batch-size", str(job["page_batch_size"])])
        if job.get("pdf_provider") == "paddle_ocr":
            command.extend(
                [
                    "--env-content-json",
                    json.dumps(
                        {
                            "pdf_parse_provider": "paddle_ocr",
                            "paddle_ocr_pipeline_version": "v1.6",
                            "paddle_ocr_vl_rec_backend": "native",
                            "paddle_ocr_vl_rec_max_concurrency": 1,
                            "paddle_ocr_use_doc_orientation_classify": False,
                            "paddle_ocr_use_doc_unwarping": False,
                            "paddle_ocr_use_queues": False,
                        }
                    ),
                ]
            )
        if job.get("force_refresh"):
            command.append("--force-refresh")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace_root),
            start_new_session=True,
        )
        job_id = str(job["id"])
        self._processes[job_id] = process
        communicate = asyncio.create_task(process.communicate())
        started = time.monotonic()
        last_progress = started
        progress_signature = self._checkpoint_signature(job_id)
        timeout_reason = ""
        poll_interval = min(
            2.0,
            float(self.parse_timeout_seconds),
            float(self.paddle_no_progress_seconds),
        )
        try:
            while not communicate.done():
                await asyncio.wait({communicate}, timeout=poll_interval)
                now = time.monotonic()
                updated_signature = self._checkpoint_signature(job_id)
                if updated_signature != progress_signature:
                    progress_signature = updated_signature
                    last_progress = now
                if now - started >= self.parse_timeout_seconds:
                    timeout_reason = (
                        f"parse exceeded hard timeout of {self.parse_timeout_seconds}s"
                    )
                    break
                if (
                    job.get("pdf_provider") == "paddle_ocr"
                    and now - last_progress >= self.paddle_no_progress_seconds
                ):
                    timeout_reason = (
                        "PaddleOCR made no batch progress for "
                        f"{self.paddle_no_progress_seconds}s"
                    )
                    break
            if timeout_reason:
                await self._terminate_process(process)
                communicate.cancel()
                await asyncio.gather(communicate, return_exceptions=True)
                return {"success": False, "error": timeout_reason}, timeout_reason, 124
            stdout_bytes, stderr_bytes = await communicate
        except asyncio.CancelledError:
            await self._terminate_process(process)
            communicate.cancel()
            await asyncio.gather(communicate, return_exceptions=True)
            raise
        except Exception:
            await self._terminate_process(process)
            communicate.cancel()
            await asyncio.gather(communicate, return_exceptions=True)
            raise
        finally:
            self._processes.pop(job_id, None)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = {"success": False, "error": "FileX returned invalid JSON"}
        return payload, stderr, int(process.returncode or 0)

    async def _startup(self, app: web.Application) -> None:
        if self.paddle_warmup:
            self._paddle_worker_state = "warming"
            self._paddle_warmup_task = asyncio.create_task(self._warm_paddle_worker())

    async def _warm_paddle_worker(self) -> None:
        try:
            async with self._paddle_worker_lock:
                await self._ensure_paddle_worker_warm()
                self._paddle_warmup_completed = True
                self._paddle_worker_state = "ready"
                self._paddle_worker_last_used_at = time.time()
                self._schedule_paddle_idle_shutdown()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Persistent PaddleOCR worker warmup failed")
            await self._stop_paddle_worker()
            self._paddle_worker_state = "failed"

    async def _execute_persistent_paddle(
        self, job: dict[str, Any]
    ) -> tuple[dict[str, Any], str, int]:
        job_id = str(job["id"])
        async with self._paddle_worker_lock:
            self._cancel_paddle_idle_shutdown()
            try:
                await self._ensure_paddle_worker_warm()
                self._paddle_worker_state = "busy"
                request = {
                    "op": "parse",
                    "workspace_path": str(
                        self.workspace_root / str(job["source_path"])
                    ),
                    "task_id": job_id,
                    "asset_reference_mode": str(job["asset_reference_mode"]),
                    "env_content": {
                        "filex_parse_provider": "paddle_ocr",
                        "pdf_parse_provider": "paddle_ocr",
                        "paddle_ocr_pipeline_version": "v1.6",
                        "paddle_ocr_vl_rec_backend": "native",
                        "paddle_ocr_vl_rec_max_concurrency": 1,
                        "paddle_ocr_use_doc_orientation_classify": False,
                        "paddle_ocr_use_doc_unwarping": False,
                        "paddle_ocr_use_layout_detection": True,
                        "paddle_ocr_use_chart_recognition": True,
                        "paddle_ocr_format_block_content": True,
                        "paddle_ocr_text_layer_formatting": True,
                        "paddle_ocr_use_queues": False,
                        "pdf_page_batch_size": job.get("page_batch_size") or 3,
                        "pdf_batch_resume_id": job_id,
                        "pdf_pages": job.get("pages"),
                        "filex_force_refresh": bool(job.get("force_refresh")),
                    },
                }
                read_task = asyncio.create_task(
                    self._send_paddle_worker_request(request)
                )
                started = time.monotonic()
                last_progress = started
                signature = self._checkpoint_signature(job_id)
                timeout_reason = ""
                while not read_task.done():
                    await asyncio.wait({read_task}, timeout=2)
                    now = time.monotonic()
                    updated_signature = self._checkpoint_signature(job_id)
                    if updated_signature != signature:
                        signature = updated_signature
                        last_progress = now
                    if now - started >= self.parse_timeout_seconds:
                        timeout_reason = (
                            "parse exceeded hard timeout of "
                            f"{self.parse_timeout_seconds}s"
                        )
                        break
                    if now - last_progress >= self.paddle_no_progress_seconds:
                        timeout_reason = (
                            "PaddleOCR made no batch progress for "
                            f"{self.paddle_no_progress_seconds}s"
                        )
                        break
                if timeout_reason:
                    read_task.cancel()
                    await asyncio.gather(read_task, return_exceptions=True)
                    await self._stop_paddle_worker()
                    return (
                        {"success": False, "error": timeout_reason},
                        timeout_reason,
                        124,
                    )
                response = await read_task
                if not response.get("ok"):
                    error = str(response.get("error") or "PaddleOCR worker failed")
                    return {"success": False, "error": error}, error, 1
                result = dict(response.get("result") or {})
                return result, "", 0 if result.get("success") else 1
            except asyncio.CancelledError:
                await self._stop_paddle_worker()
                raise
            except Exception as exc:
                await self._stop_paddle_worker()
                error = f"persistent PaddleOCR worker failed: {exc}"
                return {"success": False, "error": error}, error, 1
            finally:
                if self._paddle_worker and self._paddle_worker.returncode is None:
                    self._paddle_worker_state = "ready"
                    self._paddle_worker_last_used_at = time.time()
                    self._schedule_paddle_idle_shutdown()

    async def _ensure_paddle_worker(self) -> None:
        if self._paddle_worker and self._paddle_worker.returncode is None:
            return
        self._paddle_worker_state = "loading"
        self._paddle_worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "document_parse_service.paddle_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace_root),
            start_new_session=True,
        )
        self._paddle_worker_started_at = time.time()
        self._paddle_worker_warm = False
        self._paddle_worker_log_task = asyncio.create_task(
            self._capture_paddle_worker_logs(self._paddle_worker)
        )

    async def _ensure_paddle_worker_warm(self) -> None:
        if self._paddle_worker_warm:
            return
        last_error: Exception | None = None
        for attempt in range(1, self.paddle_warmup_max_attempts + 1):
            await self._ensure_paddle_worker()
            self._paddle_worker_state = "warming"
            try:
                response = await asyncio.wait_for(
                    self._send_paddle_worker_request({"op": "warmup"}),
                    timeout=min(
                        self.parse_timeout_seconds,
                        self.paddle_no_progress_seconds,
                        self.paddle_warmup_timeout_seconds,
                    ),
                )
                if not response.get("ok"):
                    raise RuntimeError(
                        str(response.get("error") or "warmup failed")
                    )
                self._paddle_worker_warm = True
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "PaddleOCR warmup attempt %s/%s failed: %s",
                    attempt,
                    self.paddle_warmup_max_attempts,
                    exc,
                )
                await self._stop_paddle_worker()
        raise RuntimeError(
            "PaddleOCR warmup failed after "
            f"{self.paddle_warmup_max_attempts} attempts: {last_error}"
        )

    async def _send_paddle_worker_request(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        process = self._paddle_worker
        if (
            process is None
            or process.returncode is not None
            or process.stdin is None
            or process.stdout is None
        ):
            raise RuntimeError("PaddleOCR worker is not running")
        process.stdin.write(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        )
        await process.stdin.drain()
        while True:
            line = await process.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"PaddleOCR worker exited unexpectedly with {process.returncode}"
                )
            if line.startswith(_PADDLE_RESPONSE_PREFIX):
                break
            self._append_paddle_worker_log(b"[stdout] " + line)
        response = json.loads(
            line.removeprefix(_PADDLE_RESPONSE_PREFIX).decode(
                "utf-8", errors="replace"
            )
        )
        if not isinstance(response, dict):
            raise RuntimeError("PaddleOCR worker returned an invalid response")
        return response

    async def _capture_paddle_worker_logs(
        self, process: asyncio.subprocess.Process
    ) -> None:
        if process.stderr is None:
            return
        log_path = self.service_root / "paddle-worker.log"
        with log_path.open("ab") as output:
            while line := await process.stderr.readline():
                output.write(line)
                output.flush()

    def _append_paddle_worker_log(self, line: bytes) -> None:
        log_path = self.service_root / "paddle-worker.log"
        with log_path.open("ab") as output:
            output.write(line)

    def _schedule_paddle_idle_shutdown(self) -> None:
        self._cancel_paddle_idle_shutdown()
        if self.paddle_idle_seconds == 0:
            return
        self._paddle_worker_idle_task = asyncio.create_task(
            self._stop_paddle_worker_after_idle()
        )

    def _cancel_paddle_idle_shutdown(self) -> None:
        if self._paddle_worker_idle_task and not self._paddle_worker_idle_task.done():
            self._paddle_worker_idle_task.cancel()
        self._paddle_worker_idle_task = None

    async def _stop_paddle_worker_after_idle(self) -> None:
        try:
            await asyncio.sleep(self.paddle_idle_seconds)
            async with self._paddle_worker_lock:
                await self._stop_paddle_worker()
        except asyncio.CancelledError:
            return

    async def _stop_paddle_worker(self) -> None:
        process = self._paddle_worker
        self._paddle_worker = None
        self._paddle_worker_warm = False
        self._paddle_worker_state = "stopped"
        if process and process.returncode is None:
            await self._terminate_process(process)
        if self._paddle_worker_log_task:
            await asyncio.gather(self._paddle_worker_log_task, return_exceptions=True)
        self._paddle_worker_log_task = None

    def _checkpoint_signature(self, job_id: str) -> tuple[int, int]:
        root = (
            self.workspace_root
            / "document_parse"
            / "pdf_batch_checkpoints"
            / job_id
        )
        if not root.is_dir():
            return 0, 0
        modified: list[int] = []
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    modified.append(path.stat().st_mtime_ns)
            except OSError:
                continue
        return len(modified), max(modified, default=0)

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
            return
        except TimeoutError:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        await process.wait()

    def _job_path(self, job_id: str) -> Path:
        if not re.fullmatch(r"filex-[0-9a-f]{32}", job_id):
            raise web.HTTPNotFound(text="job not found")
        return self.jobs_root / job_id / "job.json"

    def _read_job(self, job_id: str) -> dict[str, Any]:
        path = self._job_path(job_id)
        if not path.is_file():
            raise web.HTTPNotFound(text="job not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_job(self, job: dict[str, Any]) -> None:
        path = self._job_path(str(job["id"]))
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = {
            key: job.get(key)
            for key in (
                "id",
                "tenant_id",
                "status",
                "queued_at",
                "started_at",
                "completed_at",
                "filename",
                "source_sha256",
                "source_size",
                "source_mode",
                "source_fetch_ms",
                "pages",
                "provider",
                "pdf_provider",
                "page_batch_size",
                "error",
                "result",
            )
        }
        if job.get("status") == "succeeded":
            payload["artifacts"] = {
                name: f"/v1/jobs/{job['id']}/artifacts/{name}"
                for name, field in _ARTIFACT_FIELDS.items()
                if (job.get("result") or {}).get(field)
            }
            payload["artifact_files"] = self._artifact_files(job)
        return payload

    def _artifact_files(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        """List nested files next to the primary Markdown artifact."""

        result_path = str((job.get("result") or {}).get("file_path") or "").strip()
        if not result_path:
            return []
        output_path = (self.workspace_root / result_path).resolve()
        output_root = output_path.parent
        if (
            not output_root.is_relative_to(self.workspace_root)
            or not output_root.is_dir()
        ):
            return []
        primary_paths = {
            (self.workspace_root / str(value)).resolve()
            for field in _ARTIFACT_FIELDS.values()
            if (value := (job.get("result") or {}).get(field))
        }
        files: list[dict[str, Any]] = []
        for path in sorted(output_root.rglob("*")):
            resolved = path.resolve()
            if (
                not resolved.is_relative_to(output_root)
                or not path.is_file()
                or resolved in primary_paths
            ):
                continue
            relative = path.relative_to(output_root).as_posix()
            files.append(
                {
                    "path": relative,
                    "size_bytes": path.stat().st_size,
                    "download_url": (
                        f"/v1/jobs/{job['id']}/artifacts/{quote(relative, safe='/')}"
                    ),
                }
            )
        return files

    def _recover_interrupted_jobs(self) -> None:
        for path in self.jobs_root.glob("filex-*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job.get("status") not in _TERMINAL_STATUSES:
                    job["status"] = "failed"
                    job["error"] = "service restarted before the parse completed"
                    self._write_job(job)
            except Exception:  # noqa: BLE001
                logger.warning("Ignoring unreadable FileX job state: %s", path)

    async def _cleanup(self, app: web.Application) -> None:
        if self._paddle_warmup_task and not self._paddle_warmup_task.done():
            self._paddle_warmup_task.cancel()
            await asyncio.gather(self._paddle_warmup_task, return_exceptions=True)
        self._cancel_paddle_idle_shutdown()
        await self._stop_paddle_worker()
        pending = [task for task in self._tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def build_app(**kwargs: Any) -> web.Application:
    return FileXHttpService(**kwargs).create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the FileX HTTP service.")
    parser.add_argument("--host", default=os.getenv("FILEX_SERVICE_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=_int_env("FILEX_SERVICE_PORT", 18080)
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.getenv("FILEX_SERVICE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    web.run_app(build_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
