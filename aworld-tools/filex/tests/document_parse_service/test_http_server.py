import asyncio
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from document_parse_service.http_server import FileXHttpService, _is_public_ip
from document_parse_service.pdf.pdf_batch_checkpoint import PdfBatchCheckpointStore


def test_default_source_limit_is_one_gib(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FILEX_SERVICE_MAX_UPLOAD_BYTES", raising=False)
    service = FileXHttpService(workspace_root=tmp_path)

    assert service.max_upload_bytes == 1024 * 1024 * 1024


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    service = FileXHttpService(workspace_root=tmp_path, max_upload_bytes=1024)

    async def fake_execute(job):
        output = tmp_path / "document_parse" / job["id"] / "source.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# parsed", encoding="utf-8")
        document = output.with_suffix(".document.json")
        document.write_text(
            json.dumps({"schema_version": "filex-document-ir-v1", "pages": []}),
            encoding="utf-8",
        )
        asset = output.parent / "images" / "chart.png"
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        return {
            "success": True,
            "file_path": output.relative_to(tmp_path).as_posix(),
            "document_file_path": output.with_suffix(".document.json").relative_to(tmp_path).as_posix(),
        }, "", 0

    service._execute_parse = fake_execute
    async with TestClient(TestServer(service.create_app())) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_health_does_not_require_tenant(client: TestClient) -> None:
    response = await client.get("/healthz")
    assert response.status == 200
    assert (await response.json())["service"] == "filex"


@pytest.mark.asyncio
async def test_health_exposes_queue_and_timeout_controls(client: TestClient) -> None:
    response = await client.get("/healthz")
    payload = await response.json()
    assert payload["active_jobs"] == 0
    assert payload["running_jobs"] == 0
    assert payload["queued_jobs"] == 0
    assert payload["parse_timeout_seconds"] == 1800
    assert payload["paddle_no_progress_seconds"] == 300
    assert payload["paddle_idle_seconds"] == 0
    assert payload["paddle_warmup_timeout_seconds"] == 60
    assert payload["paddle_warmup_max_attempts"] == 3
    assert payload["paddle_worker_state"] == "stopped"
    assert payload["ready"] is True


@pytest.mark.asyncio
async def test_parse_does_not_require_tenant_by_default(client: TestClient) -> None:
    data = FormData()
    data.add_field("file", io.BytesIO(b"%PDF"), filename="sample.pdf")
    response = await client.post("/v1/parse", data=data)
    assert response.status == 202


@pytest.mark.asyncio
async def test_parse_can_require_configured_tenant(tmp_path: Path) -> None:
    service = FileXHttpService(workspace_root=tmp_path, tenant_id="example-tenant")
    async with TestClient(TestServer(service.create_app())) as tenant_client:
        data = FormData()
        data.add_field("file", io.BytesIO(b"%PDF"), filename="sample.pdf")
        response = await tenant_client.post("/v1/parse", data=data)
        assert response.status == 403

        data = FormData()
        data.add_field("file", io.BytesIO(b"%PDF"), filename="sample.pdf")
        response = await tenant_client.post(
            "/v1/parse",
            data=data,
            headers={"X-Tenant-ID": "example-tenant"},
        )
        assert response.status == 202


@pytest.mark.asyncio
async def test_parse_job_and_download_markdown(client: TestClient) -> None:
    data = FormData()
    data.add_field(
        "file",
        io.BytesIO(b"%PDF-1.7 sample"),
        filename="../sample.pdf",
        content_type="application/pdf",
    )
    response = await client.post(
        "/v1/parse", data=data, headers={"X-Tenant-ID": "default"}
    )
    assert response.status == 202
    created = await response.json()
    assert created["filename"] == "sample.pdf"

    for _ in range(20):
        response = await client.get(
            f"/v1/jobs/{created['id']}", headers={"X-Tenant-ID": "default"}
        )
        job = await response.json()
        if job["status"] == "succeeded":
            break
        await asyncio.sleep(0.01)
    assert job["status"] == "succeeded"

    artifact = await client.get(
        job["artifacts"]["markdown"], headers={"X-Tenant-ID": "default"}
    )
    assert artifact.status == 200
    assert await artifact.text() == "# parsed"
    document = await client.get(
        job["artifacts"]["document"], headers={"X-Tenant-ID": "default"}
    )
    assert document.status == 200
    assert (await document.json())["schema_version"] == "filex-document-ir-v1"
    assert job["artifact_files"] == [
        {
            "path": "images/chart.png",
            "size_bytes": 15,
            "download_url": (
                f"/v1/jobs/{created['id']}/artifacts/images/chart.png"
            ),
        }
    ]
    image = await client.get(
        job["artifact_files"][0]["download_url"],
        headers={"X-Tenant-ID": "default"},
    )
    assert image.status == 200
    assert await image.read() == b"\x89PNG\r\n\x1a\nfixture"


@pytest.mark.asyncio
async def test_parse_fetches_allowlisted_source_url_without_persisting_it(
    tmp_path: Path,
) -> None:
    source_payload = b"%PDF-1.7 fetched-directly"
    source_app = web.Application()
    source_app.router.add_get(
        "/private/report.pdf",
        lambda _request: web.Response(
            body=source_payload, content_type="application/pdf"
        ),
    )
    async with TestServer(source_app) as source_server:
        source_url = str(source_server.make_url("/private/report.pdf?signature=secret"))
        source_authority = urlsplit(source_url).netloc
        service = FileXHttpService(
            workspace_root=tmp_path,
            max_upload_bytes=1024,
            source_url_hosts={source_authority},
        )

        async def fake_execute(job):
            source = tmp_path / str(job["source_path"])
            assert source.read_bytes() == source_payload
            persisted_job = json.loads(
                (service.jobs_root / str(job["id"]) / "job.json").read_text()
            )
            assert persisted_job["source_fetch_ms"] >= 0
            output = tmp_path / "output.md"
            output.write_text("# parsed", encoding="utf-8")
            return {"success": True, "file_path": "output.md"}, "", 0

        service._execute_parse = fake_execute
        async with TestClient(TestServer(service.create_app())) as test_client:
            data = FormData()
            data.add_field("source_url", source_url, content_type="text/plain")
            data.add_field("source_filename", "report.pdf")
            data.add_field("source_sha256", hashlib.sha256(source_payload).hexdigest())
            data.add_field("source_size", str(len(source_payload)))
            response = await test_client.post(
                "/v1/parse", data=data, headers={"X-Tenant-ID": "default"}
            )
            assert response.status == 202
            created = await response.json()
            assert created["source_mode"] == "source_url"
            for _ in range(20):
                response = await test_client.get(
                    f"/v1/jobs/{created['id']}",
                    headers={"X-Tenant-ID": "default"},
                )
                observed = await response.json()
                if observed["status"] == "succeeded":
                    break
                await asyncio.sleep(0.01)
            assert observed["status"] == "succeeded"
            assert observed["source_fetch_ms"] >= 0
            persisted = (service.jobs_root / created["id"] / "job.json").read_text()
            assert "signature=secret" not in persisted


@pytest.mark.asyncio
async def test_parse_rejects_unallowlisted_source_url(client: TestClient) -> None:
    data = FormData()
    data.add_field(
        "source_url",
        "http://169.254.169.254/latest/meta-data",
        content_type="text/plain",
    )
    data.add_field("source_filename", "metadata.txt")
    data.add_field("source_sha256", "a" * 64)
    data.add_field("source_size", "10")
    response = await client.post(
        "/v1/parse", data=data, headers={"X-Tenant-ID": "default"}
    )
    assert response.status == 400
    assert "must use HTTPS" in await response.text()


@pytest.mark.asyncio
async def test_parse_accepts_public_pdf_url_without_declared_metadata(
    tmp_path: Path,
) -> None:
    payload = b"%PDF-1.7 public-paper"
    service = FileXHttpService(workspace_root=tmp_path, max_upload_bytes=1024)

    async def fake_download(source_url, target, *, expected_file_type):
        assert source_url == "https://arxiv.org/pdf/1706.03762"
        assert expected_file_type == "pdf"
        target.write_bytes(payload)
        return len(payload), hashlib.sha256(payload).hexdigest()

    async def fake_execute(job):
        assert job["source_size"] == len(payload)
        assert job["source_sha256"] == hashlib.sha256(payload).hexdigest()
        output = tmp_path / "output.md"
        output.write_text("# Attention Is All You Need", encoding="utf-8")
        return {"success": True, "file_path": "output.md"}, "", 0

    service._download_source_url = fake_download
    service._execute_parse = fake_execute
    async with TestClient(TestServer(service.create_app())) as test_client:
        data = FormData()
        data.add_field(
            "source_url",
            "https://arxiv.org/pdf/1706.03762",
            content_type="text/plain",
        )
        response = await test_client.post(
            "/v1/parse", data=data, headers={"X-Tenant-ID": "default"}
        )
        assert response.status == 202
        created = await response.json()
        assert created["filename"] == "1706.03762.pdf"
        assert created["source_mode"] == "public_url"
        for _ in range(20):
            response = await test_client.get(
                f"/v1/jobs/{created['id']}", headers={"X-Tenant-ID": "default"}
            )
            observed = await response.json()
            if observed["status"] == "succeeded":
                break
            await asyncio.sleep(0.01)
        assert observed["status"] == "succeeded"
        assert observed["source_size"] == len(payload)
        assert observed["source_sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_parse_accepts_public_video_url_without_declared_metadata(
    tmp_path: Path,
) -> None:
    payload = b"\x00\x00\x00\x18ftypmp42video"
    service = FileXHttpService(workspace_root=tmp_path, max_upload_bytes=1024)

    async def fake_download(source_url, target, *, expected_file_type):
        assert source_url.endswith("space_woaudio.mp4")
        assert expected_file_type == "mp4"
        target.write_bytes(payload)
        return len(payload), hashlib.sha256(payload).hexdigest()

    async def fake_execute(job):
        assert str(job["source_path"]).endswith("source.mp4")
        output = tmp_path / "output.md"
        output.write_text("# video", encoding="utf-8")
        return {"success": True, "file_path": "output.md"}, "", 0

    service._download_source_url = fake_download
    service._execute_parse = fake_execute
    async with TestClient(TestServer(service.create_app())) as test_client:
        data = FormData()
        data.add_field(
            "source_url",
            "https://example.test/space_woaudio.mp4",
            content_type="text/plain",
        )
        response = await test_client.post(
            "/v1/parse", data=data, headers={"X-Tenant-ID": "default"}
        )
        assert response.status == 202
        created = await response.json()
        assert created["filename"] == "space_woaudio.mp4"
        assert created["source_mode"] == "public_url"
        for _ in range(20):
            response = await test_client.get(
                f"/v1/jobs/{created['id']}", headers={"X-Tenant-ID": "default"}
            )
            observed = await response.json()
            if observed["status"] == "succeeded":
                break
            await asyncio.sleep(0.01)
        assert observed["status"] == "succeeded"
        assert observed["source_size"] == len(payload)


def test_public_url_address_policy() -> None:
    assert _is_public_ip("93.184.216.34") is True
    assert _is_public_ip("127.0.0.1") is False
    assert _is_public_ip("10.0.0.1") is False
    assert _is_public_ip("169.254.169.254") is False
    assert _is_public_ip("::1") is False


def test_source_url_policy_separates_public_and_trusted_hosts(tmp_path: Path) -> None:
    service = FileXHttpService(
        workspace_root=tmp_path,
        source_url_hosts={"storage.example:9000"},
    )
    assert service._validate_source_url("https://arxiv.org/pdf/1706.03762") is False
    assert service._validate_source_url("http://storage.example:9000/object.pdf") is True
    with pytest.raises(web.HTTPBadRequest) as error:
        service._validate_source_url("https://127.0.0.1/private.pdf")
    assert "host is not allowed" in error.value.text


def test_public_source_signature_supports_pdf_and_common_video_containers() -> None:
    matches = FileXHttpService._matches_public_source_signature
    assert matches("pdf", b"%PDF-1.7") is True
    assert matches("mp4", b"\x00\x00\x00\x18ftypmp42") is True
    assert matches("mkv", b"\x1a\x45\xdf\xa3") is True
    assert matches("avi", b"RIFF1234AVI ") is True
    assert matches("mp4", b"<html>not video") is False


@pytest.mark.asyncio
async def test_reads_completed_batches_after_cursor(tmp_path: Path) -> None:
    service = FileXHttpService(workspace_root=tmp_path, max_upload_bytes=1024)
    job_id = "filex-00000000000000000000000000000001"
    job_dir = service.jobs_root / job_id
    job_dir.mkdir(parents=True)
    service._write_job(
        {
            "id": job_id,
            "tenant_id": "default",
            "status": "running",
            "filename": "sample.pdf",
            "result": None,
            "error": None,
        }
    )
    store = PdfBatchCheckpointStore(
        tmp_path / "document_parse" / "pdf_batch_checkpoints", job_id
    )
    store._directory.mkdir(parents=True)
    (store._directory / "progress.json").write_text(
        json.dumps({"status": "running", "total_batches": 2}), encoding="utf-8"
    )
    (store._directory / "batch-1.json").write_text(
        json.dumps(
            {
                "status": "succeeded",
                "pages": [1, 2, 3],
                "artifact": {"markdown_text": "# first", "assets": []},
            }
        ),
        encoding="utf-8",
    )

    async with TestClient(TestServer(service.create_app())) as test_client:
        response = await test_client.get(
            f"/v1/jobs/{job_id}/batches?after=0&limit=1",
            headers={"X-Tenant-ID": "default"},
        )
        assert response.status == 200
        payload = await response.json()
        assert payload["cursor"] == 1
        assert payload["is_final"] is False
        assert payload["batches"][0]["markdown"] == "# first"

        response = await test_client.get(
            f"/v1/jobs/{job_id}/batches?after=1",
            headers={"X-Tenant-ID": "default"},
        )
        assert (await response.json())["batches"] == []


@pytest.mark.asyncio
async def test_rejects_invalid_page_selection(client: TestClient) -> None:
    data = FormData()
    data.add_field("pages", "1;rm -rf")
    data.add_field("file", io.BytesIO(b"%PDF"), filename="sample.pdf")
    response = await client.post(
        "/v1/parse", data=data, headers={"X-Tenant-ID": "default"}
    )
    assert response.status == 400


@pytest.mark.asyncio
async def test_rejects_unknown_pdf_provider(client: TestClient) -> None:
    data = FormData()
    data.add_field("pdf_provider", "magic")
    data.add_field("file", io.BytesIO(b"%PDF"), filename="sample.pdf")
    response = await client.post(
        "/v1/parse", data=data, headers={"X-Tenant-ID": "default"}
    )
    assert response.status == 400


@pytest.mark.asyncio
async def test_rejects_upload_when_public_queue_is_full(tmp_path: Path) -> None:
    service = FileXHttpService(
        workspace_root=tmp_path,
        max_upload_bytes=1024,
        max_pending_jobs=1,
    )
    release = asyncio.Event()

    async def slow_execute(job):
        await release.wait()
        return {"success": True, "file_path": "output.md"}, "", 0

    service._execute_parse = slow_execute
    async with TestClient(TestServer(service.create_app())) as test_client:
        first = FormData()
        first.add_field("file", io.BytesIO(b"%PDF"), filename="first.pdf")
        response = await test_client.post(
            "/v1/parse", data=first, headers={"X-Tenant-ID": "default"}
        )
        assert response.status == 202

        second = FormData()
        second.add_field("file", io.BytesIO(b"%PDF"), filename="second.pdf")
        response = await test_client.post(
            "/v1/parse", data=second, headers={"X-Tenant-ID": "default"}
        )
        assert response.status == 429
        assert response.headers["Retry-After"] == "30"
        release.set()


@pytest.mark.asyncio
async def test_cancel_releases_running_job_slot(tmp_path: Path) -> None:
    service = FileXHttpService(
        workspace_root=tmp_path,
        max_upload_bytes=1024,
        max_pending_jobs=1,
    )
    started = asyncio.Event()

    async def slow_execute(job):
        started.set()
        await asyncio.Event().wait()

    service._execute_parse = slow_execute
    async with TestClient(TestServer(service.create_app())) as test_client:
        first = FormData()
        first.add_field("file", io.BytesIO(b"%PDF"), filename="first.pdf")
        response = await test_client.post(
            "/v1/parse", data=first, headers={"X-Tenant-ID": "default"}
        )
        created = await response.json()
        await asyncio.wait_for(started.wait(), timeout=1)

        response = await test_client.delete(
            f"/v1/jobs/{created['id']}", headers={"X-Tenant-ID": "default"}
        )
        assert response.status == 200
        assert (await response.json())["status"] == "cancelled"

        health = await (await test_client.get("/healthz")).json()
        assert health["active_jobs"] == 0

        second = FormData()
        second.add_field("file", io.BytesIO(b"%PDF"), filename="second.pdf")
        response = await test_client.post(
            "/v1/parse", data=second, headers={"X-Tenant-ID": "default"}
        )
        assert response.status == 202


@pytest.mark.asyncio
async def test_paddle_no_progress_timeout_terminates_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        pid = 12345
        returncode = None

    service = FileXHttpService(
        workspace_root=tmp_path,
        parse_timeout_seconds=30,
        paddle_no_progress_seconds=1,
    )
    service._paddle_worker = Process()

    async def fake_ensure():
        return None

    async def blocked_request(payload):
        await asyncio.Event().wait()

    async def fake_stop():
        service._paddle_worker = None
        service._paddle_worker_state = "stopped"

    monkeypatch.setattr(service, "_ensure_paddle_worker_warm", fake_ensure)
    monkeypatch.setattr(service, "_send_paddle_worker_request", blocked_request)
    monkeypatch.setattr(service, "_stop_paddle_worker", fake_stop)
    payload, stderr, returncode = await service._execute_persistent_paddle(
        {
            "id": "filex-00000000000000000000000000000000",
            "source_path": "source.pdf",
            "asset_reference_mode": "local_path",
            "pdf_provider": "paddle_ocr",
            "page_batch_size": 3,
            "pages": None,
            "force_refresh": False,
        }
    )
    assert returncode == 124
    assert stderr == "PaddleOCR made no batch progress for 1s"
    assert payload["error"] == stderr


@pytest.mark.asyncio
async def test_paddle_jobs_use_persistent_worker(
    tmp_path: Path,
) -> None:
    service = FileXHttpService(workspace_root=tmp_path)
    captured = []

    async def fake_persistent(job):
        captured.append(job)
        return {"success": True}, "", 0

    service._execute_persistent_paddle = fake_persistent
    result = await service._execute_parse(
        {"id": "filex-00000000000000000000000000000000", "pdf_provider": "paddle_ocr"}
    )
    assert result == ({"success": True}, "", 0)
    assert captured[0]["id"] == "filex-00000000000000000000000000000000"


@pytest.mark.asyncio
async def test_paddle_warmup_retries_with_a_fresh_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        returncode = None

        def __init__(self, pid: int) -> None:
            self.pid = pid

    service = FileXHttpService(
        workspace_root=tmp_path,
        paddle_warmup_max_attempts=3,
    )
    worker_count = 0
    request_count = 0

    async def fake_ensure() -> None:
        nonlocal worker_count
        if service._paddle_worker is None:
            worker_count += 1
            service._paddle_worker = Process(worker_count)

    async def fake_request(payload):
        nonlocal request_count
        assert payload == {"op": "warmup"}
        request_count += 1
        if request_count == 1:
            raise RuntimeError("first inference stalled")
        return {"ok": True, "result": {"warm": True}}

    async def fake_stop() -> None:
        service._paddle_worker = None
        service._paddle_worker_warm = False
        service._paddle_worker_state = "stopped"

    monkeypatch.setattr(service, "_ensure_paddle_worker", fake_ensure)
    monkeypatch.setattr(service, "_send_paddle_worker_request", fake_request)
    monkeypatch.setattr(service, "_stop_paddle_worker", fake_stop)

    await service._ensure_paddle_worker_warm()

    assert service._paddle_worker_warm is True
    assert worker_count == 2
    assert request_count == 2
