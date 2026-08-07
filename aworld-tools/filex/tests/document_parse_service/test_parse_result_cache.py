import asyncio
import types
import uuid
from pathlib import Path

from document_parse_service.parse_result_cache import (
    ParseResultCache,
    build_parse_cache_key,
)
from document_parse_service.service import DocumentParseService


def _result(task_id: str = "source-task") -> dict:
    return {
        "success": True,
        "message": "Document parsed successfully",
        "task_id": task_id,
        "metrics": {
            "timings_ms": {"initialization": 200, "parse": 1000, "total": 1234},
            "model": {
                "name": "ocr-model",
                "call_count": 18,
                "retry_count": 2,
                "peak_concurrency": 4,
                "timeout_count": 1,
            },
        },
    }


def test_cache_reuses_successful_result_and_reports_saved_time() -> None:
    asyncio.run(_assert_cache_reuses_successful_result())


async def _assert_cache_reuses_successful_result() -> None:
    cache_root = Path("/tmp") / f"filex-cache-test-{uuid.uuid4()}"
    cache = ParseResultCache(cache_root)
    calls = 0

    async def compute() -> dict:
        nonlocal calls
        calls += 1
        return _result()

    first = await cache.get_or_compute(
        key="key",
        task_id="first-task",
        compute=compute,
        ttl_seconds=60,
        max_entries=10,
        force_refresh=False,
    )
    second = await ParseResultCache(cache_root).get_or_compute(
        key="key",
        task_id="second-task",
        compute=compute,
        ttl_seconds=60,
        max_entries=10,
        force_refresh=False,
    )

    assert calls == 1
    assert first["metrics"]["cache"]["status"] == "miss"
    assert second["task_id"] == "second-task"
    assert second["metrics"]["cache"]["status"] == "hit"
    assert second["metrics"]["cache"]["saved_duration_ms"] == 1234
    assert second["metrics"]["cache"]["source_task_id"] == "source-task"
    assert second["metrics"]["cache"]["source_timings_ms"]["parse"] == 1000
    assert second["metrics"]["cache"]["source_model"]["call_count"] == 18
    assert second["metrics"]["timings_ms"]["parse"] == 0
    assert second["metrics"]["model"]["call_count"] == 0
    assert second["metrics"]["model"]["retry_count"] == 0
    assert second["metrics"]["model"]["timeout_count"] == 0


def test_single_flight_only_computes_once_for_concurrent_requests() -> None:
    asyncio.run(_assert_single_flight_only_computes_once())


async def _assert_single_flight_only_computes_once() -> None:
    cache = ParseResultCache(Path("/tmp") / f"filex-cache-test-{uuid.uuid4()}")
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def compute() -> dict:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _result()

    first = asyncio.create_task(
        cache.get_or_compute(
            key="key",
            task_id="first",
            compute=compute,
            ttl_seconds=60,
            max_entries=10,
            force_refresh=False,
        )
    )
    await started.wait()
    second = asyncio.create_task(
        cache.get_or_compute(
            key="key",
            task_id="second",
            compute=compute,
            ttl_seconds=60,
            max_entries=10,
            force_refresh=False,
        )
    )
    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert calls == 1
    statuses = {
        first_result["metrics"]["cache"]["status"],
        second_result["metrics"]["cache"]["status"],
    }
    assert statuses == {
        "miss",
        "hit",
    }


def test_force_refresh_replaces_cached_result() -> None:
    asyncio.run(_assert_force_refresh_replaces_cached_result())


async def _assert_force_refresh_replaces_cached_result() -> None:
    cache = ParseResultCache(Path("/tmp") / f"filex-cache-test-{uuid.uuid4()}")
    calls = 0

    async def compute() -> dict:
        nonlocal calls
        calls += 1
        return _result(f"source-{calls}")

    common = {
        "key": "key",
        "task_id": "request",
        "compute": compute,
        "ttl_seconds": 60,
        "max_entries": 10,
    }
    await cache.get_or_compute(**common, force_refresh=False)
    refreshed = await cache.get_or_compute(**common, force_refresh=True)
    hit = await cache.get_or_compute(**common, force_refresh=False)

    assert calls == 2
    assert refreshed["metrics"]["cache"]["status"] == "miss"
    assert refreshed["metrics"]["cache"]["forced_refresh"] is True
    assert hit["metrics"]["cache"]["source_task_id"] == "source-2"


def test_cache_key_tracks_local_file_changes_and_redacts_secret(tmp_path: Path) -> None:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"first")
    env = {
        "providers": {"pdf": "paddle_ocr"},
        "gateway_vllm": {"model_name": "model-a", "api_key": "secret-a"},
    }
    first = build_parse_cache_key(
        file_id="",
        workspace_path=str(source),
        file_type="pdf",
        asset_reference_mode="remote_id",
        env_content=env,
    )
    env["gateway_vllm"]["api_key"] = "secret-b"
    same_without_secret = build_parse_cache_key(
        file_id="",
        workspace_path=str(source),
        file_type="pdf",
        asset_reference_mode="remote_id",
        env_content=env,
    )
    source.write_bytes(b"changed")
    changed_file = build_parse_cache_key(
        file_id="",
        workspace_path=str(source),
        file_type="pdf",
        asset_reference_mode="remote_id",
        env_content=env,
    )

    assert first == same_without_secret
    assert first != changed_file


def test_cache_key_separates_provider_pages_and_model() -> None:
    common = {
        "file_id": "file-1",
        "workspace_path": "",
        "file_type": "pdf",
        "asset_reference_mode": "remote_id",
    }
    liteparse = build_parse_cache_key(
        **common,
        env_content={
            "pdf_parse_provider": "liteparse",
            "pdf_pages": "1-5",
            "gateway_vllm": {"model_name": "model-a"},
        },
    )
    paddle = build_parse_cache_key(
        **common,
        env_content={
            "pdf_parse_provider": "paddle_ocr",
            "pdf_pages": "1-5",
            "gateway_vllm": {"model_name": "model-a"},
        },
    )
    other_pages = build_parse_cache_key(
        **common,
        env_content={
            "pdf_parse_provider": "liteparse",
            "pdf_pages": "6-10",
            "gateway_vllm": {"model_name": "model-a"},
        },
    )
    other_model = build_parse_cache_key(
        **common,
        env_content={
            "pdf_parse_provider": "liteparse",
            "pdf_pages": "1-5",
            "gateway_vllm": {"model_name": "model-b"},
        },
    )

    assert len({liteparse, paddle, other_pages, other_model}) == 4


def test_document_parse_service_cache_and_no_cache_switch(tmp_path: Path) -> None:
    asyncio.run(_assert_document_parse_service_cache_and_no_cache_switch(tmp_path))


async def _assert_document_parse_service_cache_and_no_cache_switch(tmp_path: Path) -> None:
    service = DocumentParseService()
    calls = 0

    async def fake_parse_uncached(self: DocumentParseService, **kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        return _result(f"source-{calls}")

    service._parse_uncached = types.MethodType(  # type: ignore[method-assign]
        fake_parse_uncached,
        service,
    )
    source_path = tmp_path / f"cache-test-{uuid.uuid4()}.pdf"
    source_path.write_bytes(b"%PDF-1.4\n")
    workspace_path = str(source_path)
    first = await service.parse(workspace_path=workspace_path, file_type="pdf", task_id="first")
    second = await service.parse(workspace_path=workspace_path, file_type="pdf", task_id="second")
    bypass = await service.parse(
        workspace_path=workspace_path,
        file_type="pdf",
        task_id="bypass",
        env_content={"filex_no_cache": True},
    )

    assert calls == 2
    assert first["metrics"]["cache"]["status"] == "miss"
    assert second["metrics"]["cache"]["status"] == "hit"
    assert bypass["metrics"]["cache"]["status"] == "bypass"
