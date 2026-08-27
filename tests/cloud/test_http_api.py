from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path, PurePosixPath

from fastapi.testclient import TestClient

from aworld.cloud.fake_executor import (
    FakeCloudExecutor,
    FakeEventSpec,
    FakeExecutionPlan,
)
from aworld.cloud.models import (
    BenchmarkOutcome,
    FileId,
    RunFile,
    RunFileKind,
    RunId,
    utc_now,
)
from aworld.cloud.service import CloudService
from aworld.cloud.settings import CloudSettings, WorkspaceProfile
from aworld.cloud.sqlite_repository import SQLiteCloudRepository
from aworld.cloud.worker import CloudWorker
from aworld_gateway.http.cloud_router import CloudApiDependencies
from aworld_gateway.http.server import create_gateway_app

_OFFSET_PATTERN = re.compile(r"[+-]\d{2}:\d{2}\Z")


@dataclass
class _SequentialIds:
    value: int = 0

    def __call__(self) -> str:
        self.value += 1
        return f"api-{self.value}"


@dataclass
class _ApiStack:
    settings: CloudSettings
    repository: SQLiteCloudRepository
    service: CloudService
    executor: FakeCloudExecutor
    worker: CloudWorker
    client: TestClient


def _settings(tmp_path: Path, *, enabled: bool = True) -> CloudSettings:
    image = "registry.example/codex@sha256:api"
    profile = WorkspaceProfile(
        name="aworld-development",
        writable_repo_root=tmp_path / "workspaces",
        runtime_image=image,
    )
    return CloudSettings(
        enabled=enabled,
        data_root=tmp_path / "cloud-data",
        database_path=tmp_path / "cloud-data" / "cloud.sqlite3",
        worker_id="api-worker",
        concurrency=2,
        lease_duration=timedelta(seconds=2),
        heartbeat_interval=timedelta(milliseconds=50),
        poll_interval=timedelta(milliseconds=5),
        allowed_profiles={profile.name: profile},
        allowed_images=frozenset({image}),
    )


def _database_path(settings: CloudSettings) -> Path:
    assert settings.database_path is not None
    return settings.database_path


def _open_repository(settings: CloudSettings) -> SQLiteCloudRepository:
    repository = SQLiteCloudRepository(_database_path(settings))
    asyncio.run(repository.initialize())
    return repository


def _build_stack(tmp_path: Path) -> _ApiStack:
    settings = _settings(tmp_path)
    repository = _open_repository(settings)
    service = CloudService(repository, settings, id_factory=_SequentialIds())
    executor = FakeCloudExecutor()
    worker = CloudWorker(repository, executor, settings)
    app = create_gateway_app(
        runtime_status={"channels": {}},
        cloud_api=CloudApiDependencies(service, repository, settings),
    )
    return _ApiStack(
        settings=settings,
        repository=repository,
        service=service,
        executor=executor,
        worker=worker,
        client=TestClient(app),
    )


def _close(stack: _ApiStack) -> None:
    stack.client.close()
    asyncio.run(stack.repository.close())


def _create_workspace(
    client: TestClient,
    *,
    name: str = "API workspace",
    key: str = "workspace-key",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/cloud/workspaces",
        json={
            "name": name,
            "profile_name": "aworld-development",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return response.json()


def _submit_run(
    client: TestClient,
    workspace_id: str,
    *,
    task: str = "exercise API",
    key: str = "run-key",
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/cloud/workspaces/{workspace_id}/runs",
        json={
            "task": task,
            "model": "codex-api-test",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_cloud_routes_are_opt_in_and_workspace_contracts_are_stable(
    tmp_path: Path,
) -> None:
    default_app = create_gateway_app(runtime_status={"channels": {}})
    with TestClient(default_app) as default_client:
        assert default_client.get("/api/v1/cloud/workspaces").status_code == 404

    disabled_settings = _settings(tmp_path / "disabled", enabled=False)
    disabled_repository = _open_repository(disabled_settings)
    disabled_service = CloudService(disabled_repository, disabled_settings)
    disabled_app = create_gateway_app(
        runtime_status={"channels": {}},
        cloud_api=CloudApiDependencies(
            disabled_service,
            disabled_repository,
            disabled_settings,
        ),
    )
    with TestClient(disabled_app) as disabled_client:
        assert disabled_client.get("/api/v1/cloud/workspaces").status_code == 404
    asyncio.run(disabled_repository.close())

    stack = _build_stack(tmp_path / "enabled")
    try:
        workspaces = [
            _create_workspace(
                stack.client,
                name=f"Workspace {index}",
                key=f"workspace-{index}",
            )
            for index in range(3)
        ]
        first = workspaces[0]
        assert first["state"] == "ready"
        assert _OFFSET_PATTERN.search(str(first["created_at"]))
        assert _OFFSET_PATTERN.search(str(first["updated_at"]))
        assert first["mounts"] == [
            {"container_path": "/workspace/aworld", "access_mode": "rw"},
            {"container_path": "/home/node/.codex", "access_mode": "rw"},
        ]
        assert "host_path" not in first
        assert "codex_home_path" not in first

        first_page = stack.client.get("/api/v1/cloud/workspaces", params={"limit": 2})
        assert first_page.status_code == 200
        first_payload = first_page.json()
        assert len(first_payload["items"]) == 2
        assert first_payload["next_page_token"]
        second_page = stack.client.get(
            "/api/v1/cloud/workspaces",
            params={
                "limit": 2,
                "page_token": first_payload["next_page_token"],
            },
        )
        assert [item["id"] for item in second_page.json()["items"]] == [
            workspaces[2]["id"]
        ]

        runs = [
            _submit_run(
                stack.client,
                str(first["id"]),
                task=f"paged run {index}",
                key=f"paged-run-{index}",
            )
            for index in range(3)
        ]
        assert runs[0]["request_schema_version"] == "aworld.cloud.run-request.v1"
        assert runs[0]["mode"] == "query"
        assert runs[0]["benchmark"] is None
        first_run_page = stack.client.get(
            "/api/v1/cloud/runs",
            params={"limit": 2, "workspace_id": first["id"]},
        ).json()
        assert [item["id"] for item in first_run_page["items"]] == [
            runs[0]["id"],
            runs[1]["id"],
        ]
        second_run_page = stack.client.get(
            "/api/v1/cloud/runs",
            params={
                "limit": 2,
                "workspace_id": first["id"],
                "page_token": first_run_page["next_page_token"],
            },
        ).json()
        assert [item["id"] for item in second_run_page["items"]] == [runs[2]["id"]]

        invalid_event_id = stack.client.get(
            f"/api/v1/cloud/runs/{runs[0]['id']}/events/stream",
            headers={"Last-Event-ID": "not-a-sequence"},
        )
        assert invalid_event_id.status_code == 400
        assert invalid_event_id.json()["error"]["code"] == "invalid_request"

        invalid_page = stack.client.get(
            "/api/v1/cloud/workspaces", params={"page_token": "not-a-token"}
        )
        assert invalid_page.status_code == 400
        assert invalid_page.json()["error"]["code"] == "invalid_request"

        missing = stack.client.get("/api/v1/cloud/workspaces/missing")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "workspace_not_found"

        conflict = stack.client.post(
            "/api/v1/cloud/workspaces",
            json={
                "name": "Changed payload",
                "profile_name": "aworld-development",
                "idempotency_key": "workspace-0",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "idempotency_conflict"

        invalid = stack.client.post(
            "/api/v1/cloud/workspaces",
            json={"name": "missing fields", "unexpected": True},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "invalid_request"
    finally:
        _close(stack)


def test_run_events_sse_and_manifest_file_ranges(tmp_path: Path) -> None:
    stack = _build_stack(tmp_path)
    try:
        workspace = _create_workspace(stack.client)
        workspace_id = str(workspace["id"])
        run_payload = _submit_run(stack.client, workspace_id)
        run_id = RunId(str(run_payload["id"]))
        output = stack.settings.data_root / "runs" / run_id
        output.mkdir(parents=True, exist_ok=True)
        file_bytes = b'{"ok":true}\n'
        result_path = output / "result.json"
        result_path.write_bytes(file_bytes)
        run_file = RunFile(
            id=FileId("file-result"),
            run_id=run_id,
            kind=RunFileKind.RESULT,
            relative_path=PurePosixPath("result.json"),
            size_bytes=len(file_bytes),
            sha256=hashlib.sha256(file_bytes).hexdigest(),
            created_at=utc_now(),
        )
        stack.executor.set_plan(
            run_id,
            FakeExecutionPlan(
                events=(
                    FakeEventSpec("executor.progress", {"step": 1}),
                    FakeEventSpec("executor.unknown", {"raw": {"kind": "new"}}),
                ),
                files=(run_file,),
            ),
        )

        asyncio.run(stack.worker.run_until_idle())

        run_response = stack.client.get(f"/api/v1/cloud/runs/{run_id}")
        assert run_response.status_code == 200
        terminal = run_response.json()
        assert terminal["state"] == "succeeded"
        assert terminal["file_count"] == 2
        assert terminal["canonical_trajectory_file_id"]
        assert terminal["duration_seconds"] is not None
        assert _OFFSET_PATTERN.search(terminal["started_at"])
        assert _OFFSET_PATTERN.search(terminal["finished_at"])

        listed_runs = stack.client.get(
            "/api/v1/cloud/runs",
            params={"workspace_id": workspace_id, "state": "succeeded"},
        )
        assert listed_runs.status_code == 200
        assert [item["id"] for item in listed_runs.json()["items"]] == [str(run_id)]

        first_events = stack.client.get(
            f"/api/v1/cloud/runs/{run_id}/events",
            params={"limit": 2},
        )
        assert first_events.status_code == 200
        first_event_payload = first_events.json()
        assert [item["sequence"] for item in first_event_payload["items"]] == [1, 2]
        assert first_event_payload["next_after_sequence"] == 2
        later_events = stack.client.get(
            f"/api/v1/cloud/runs/{run_id}/events",
            params={"after_sequence": 2, "limit": 100},
        )
        later_sequences = [item["sequence"] for item in later_events.json()["items"]]
        assert later_sequences == list(range(3, later_sequences[-1] + 1))

        stream = stack.client.get(
            f"/api/v1/cloud/runs/{run_id}/events/stream",
            headers={"Last-Event-ID": "2"},
        )
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        streamed_ids = [
            int(line.removeprefix("id: "))
            for line in stream.text.splitlines()
            if line.startswith("id: ")
        ]
        assert streamed_ids == later_sequences
        assert "+00:00" in stream.text

        files = stack.client.get(f"/api/v1/cloud/runs/{run_id}/files")
        assert files.status_code == 200
        listed_file = files.json()["items"][0]
        assert listed_file["relative_path"] == "result.json"
        assert listed_file["sha256"] == hashlib.sha256(file_bytes).hexdigest()
        trajectory_file = files.json()["items"][1]
        assert trajectory_file["kind"] == "trajectory"
        assert trajectory_file["trajectory"] == {
            "format": "atif",
            "schema_version": "ATIF-v1.7",
            "role": "canonical",
        }

        full = stack.client.get(listed_file["download_url"])
        assert full.status_code == 200
        assert full.content == file_bytes
        assert full.headers["accept-ranges"] == "bytes"
        partial = stack.client.get(
            listed_file["download_url"], headers={"Range": "bytes=1-4"}
        )
        assert partial.status_code == 206
        assert partial.content == file_bytes[1:5]
        assert partial.headers["content-range"] == f"bytes 1-4/{len(file_bytes)}"

        invalid_range = stack.client.get(
            listed_file["download_url"], headers={"Range": "bytes=999-1000"}
        )
        assert invalid_range.status_code == 416
        assert invalid_range.json()["error"]["code"] == "invalid_request"

        unmanifested = output / "unmanifested.txt"
        unmanifested.write_text("secret", encoding="utf-8")
        absent = stack.client.get(
            f"/api/v1/cloud/runs/{run_id}/files/file-unmanifested"
        )
        assert absent.status_code == 404
        assert absent.json()["error"]["code"] == "file_not_found"

        outside = tmp_path / "outside.log"
        outside.write_text("outside", encoding="utf-8")
        (output / "escape.log").symlink_to(outside)
        escaped_file = RunFile(
            id=FileId("file-escape"),
            run_id=run_id,
            kind=RunFileKind.STDERR,
            relative_path=PurePosixPath("escape.log"),
            size_bytes=outside.stat().st_size,
            sha256=hashlib.sha256(outside.read_bytes()).hexdigest(),
            created_at=utc_now(),
        )
        asyncio.run(stack.repository.register_run_file(escaped_file))
        escaped = stack.client.get(
            f"/api/v1/cloud/runs/{run_id}/files/{escaped_file.id}"
        )
        assert escaped.status_code == 404
        assert escaped.json()["error"]["code"] == "file_not_found"
    finally:
        _close(stack)


def test_batch_http_path_converges_from_two_real_worker_runs(tmp_path: Path) -> None:
    stack = _build_stack(tmp_path)
    try:
        workspace = _create_workspace(stack.client)
        created = stack.client.post(
            f"/api/v1/cloud/workspaces/{workspace['id']}/batches",
            json={
                "name": "mixed smoke",
                "idempotency_key": "batch-mixed",
                "runs": [
                    {"task": "succeed", "model": "codex-api-test"},
                    {"task": "fail", "model": "codex-api-test"},
                ],
            },
        )
        assert created.status_code == 201
        batch = created.json()
        assert batch["state"] == "queued"
        assert batch["counts"]["total"] == 2

        run_page = stack.client.get(f"/api/v1/cloud/batches/{batch['id']}/runs")
        assert run_page.status_code == 200
        runs = run_page.json()["items"]
        assert all(run["batch_id"] == batch["id"] for run in runs)
        stack.executor.set_plan(RunId(runs[0]["id"]), FakeExecutionPlan())
        stack.executor.set_plan(
            RunId(runs[1]["id"]),
            FakeExecutionPlan(exit_code=7, error_code="test_failure"),
        )

        asyncio.run(stack.worker.run_until_idle())

        terminal = stack.client.get(f"/api/v1/cloud/batches/{batch['id']}").json()
        assert terminal["state"] == "partially_succeeded"
        assert terminal["progress"] == 1.0
        assert terminal["counts"]["succeeded"] == 1
        assert terminal["counts"]["failed"] == 1
        assert terminal["finished_at"] is not None
        listed = stack.client.get("/api/v1/cloud/batches").json()["items"]
        assert [item["id"] for item in listed] == [batch["id"]]

        cancellable = stack.client.post(
            f"/api/v1/cloud/workspaces/{workspace['id']}/batches",
            json={
                "name": "cancel smoke",
                "idempotency_key": "batch-cancel-create",
                "runs": [{"task": "queued one"}, {"task": "queued two"}],
            },
        ).json()
        cancel_url = f"/api/v1/cloud/batches/{cancellable['id']}/cancel"
        cancelled = stack.client.post(
            cancel_url, json={"idempotency_key": "batch-cancel"}
        )
        repeated = stack.client.post(
            cancel_url, json={"idempotency_key": "batch-cancel"}
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["counts"]["cancelled"] == 2
        assert repeated.json() == cancelled.json()

        first_page = stack.client.get(
            "/api/v1/cloud/batches", params={"limit": 1}
        ).json()
        second_page = stack.client.get(
            "/api/v1/cloud/batches",
            params={"limit": 1, "page_token": first_page["next_page_token"]},
        ).json()
        assert [item["id"] for item in first_page["items"]] == [batch["id"]]
        assert [item["id"] for item in second_page["items"]] == [cancellable["id"]]

        missing = stack.client.get("/api/v1/cloud/batches/missing")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "batch_not_found"
    finally:
        _close(stack)


def test_run_mode_contract_accepts_benchmark_and_rejects_cross_mode_metadata(
    tmp_path: Path,
) -> None:
    stack = _build_stack(tmp_path)
    try:
        workspace = _create_workspace(stack.client)
        workspace_id = str(workspace["id"])
        benchmark = stack.client.post(
            f"/api/v1/cloud/workspaces/{workspace_id}/runs",
            json={
                "idempotency_key": "benchmark-run",
                "mode": "benchmark",
                "task": "execute benchmark case",
                "benchmark": {
                    "dataset": "swe-bench",
                    "task_id": "case-1",
                    "harness": "custom",
                    "verifier": "patch",
                },
            },
        )
        assert benchmark.status_code == 201
        assert benchmark.json()["mode"] == "benchmark"
        assert benchmark.json()["benchmark"]["dataset"] == "swe-bench"
        benchmark_run_id = RunId(benchmark.json()["id"])
        stack.executor.set_plan(
            benchmark_run_id,
            FakeExecutionPlan(
                benchmark_outcome=BenchmarkOutcome(
                    reward=0.5,
                    result={"passed": True, "verdict": "accepted"},
                )
            ),
        )
        asyncio.run(stack.worker.run_until_idle())
        terminal = stack.client.get(f"/api/v1/cloud/runs/{benchmark_run_id}").json()
        assert terminal["benchmark_outcome"] == {
            "reward": 0.5,
            "result": {"passed": True, "verdict": "accepted"},
        }
        assert terminal["canonical_trajectory_file_id"]

        query_with_benchmark = stack.client.post(
            f"/api/v1/cloud/workspaces/{workspace_id}/runs",
            json={
                "idempotency_key": "invalid-query",
                "task": "query",
                "benchmark": {"dataset": "d", "task_id": "t"},
            },
        )
        assert query_with_benchmark.status_code == 422
        assert query_with_benchmark.json()["error"]["code"] == "invalid_request"

        benchmark_without_metadata = stack.client.post(
            f"/api/v1/cloud/workspaces/{workspace_id}/runs",
            json={
                "idempotency_key": "invalid-benchmark",
                "mode": "benchmark",
                "task": "benchmark",
            },
        )
        assert benchmark_without_metadata.status_code == 422

        blank_benchmark_identity = stack.client.post(
            f"/api/v1/cloud/workspaces/{workspace_id}/runs",
            json={
                "idempotency_key": "invalid-blank-benchmark",
                "mode": "benchmark",
                "task": "benchmark",
                "benchmark": {"dataset": "   ", "task_id": "t"},
            },
        )
        assert blank_benchmark_identity.status_code == 422

        client_supplied_outcome = stack.client.post(
            f"/api/v1/cloud/workspaces/{workspace_id}/runs",
            json={
                "idempotency_key": "invalid-client-outcome",
                "mode": "benchmark",
                "task": "benchmark",
                "benchmark": {"dataset": "d", "task_id": "t"},
                "benchmark_outcome": {"reward": 1.0, "result": {}},
            },
        )
        assert client_supplied_outcome.status_code == 422
    finally:
        _close(stack)


def test_cancel_retry_release_and_domain_error_mapping(tmp_path: Path) -> None:
    stack = _build_stack(tmp_path)
    try:
        queued_workspace = _create_workspace(
            stack.client,
            name="Queued cancellation",
            key="queued-workspace",
        )
        queued_run = _submit_run(
            stack.client,
            str(queued_workspace["id"]),
            task="cancel me",
            key="queued-run",
        )
        cancel_url = f"/api/v1/cloud/runs/{queued_run['id']}/cancel"
        cancelled = stack.client.post(
            cancel_url, json={"idempotency_key": "cancel-key"}
        )
        repeated_cancel = stack.client.post(
            cancel_url, json={"idempotency_key": "cancel-key"}
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert repeated_cancel.json() == cancelled.json()

        release = stack.client.request(
            "DELETE",
            f"/api/v1/cloud/workspaces/{queued_workspace['id']}",
            json={"idempotency_key": "release-key"},
        )
        assert release.status_code == 200
        assert release.json()["state"] == "released"

        failed_workspace = _create_workspace(
            stack.client,
            name="Failed retry",
            key="failed-workspace",
        )
        failed_run = _submit_run(
            stack.client,
            str(failed_workspace["id"]),
            task="fail me",
            key="failed-run",
        )
        stack.executor.set_plan(
            RunId(str(failed_run["id"])),
            FakeExecutionPlan(exit_code=7),
        )
        asyncio.run(stack.worker.run_until_idle())
        retry = stack.client.post(
            f"/api/v1/cloud/runs/{failed_run['id']}/retry",
            json={"idempotency_key": "retry-key"},
        )
        assert retry.status_code == 201
        assert retry.json()["state"] == "queued"
        assert retry.json()["attempt"] == 2
        assert retry.json()["retry_of_run_id"] == failed_run["id"]

        invalid_retry = stack.client.post(
            f"/api/v1/cloud/runs/{retry.json()['id']}/retry",
            json={"idempotency_key": "invalid-retry"},
        )
        assert invalid_retry.status_code == 409
        assert invalid_retry.json()["error"]["code"] == "invalid_transition"
    finally:
        _close(stack)


def test_api_application_recreation_uses_persisted_sqlite_state(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    first_repository = _open_repository(settings)
    first_service = CloudService(
        first_repository,
        settings,
        id_factory=_SequentialIds(),
    )
    first_app = create_gateway_app(
        runtime_status={"channels": {}},
        cloud_api=CloudApiDependencies(first_service, first_repository, settings),
    )
    with TestClient(first_app) as first_client:
        workspace = _create_workspace(first_client)
        run = _submit_run(first_client, str(workspace["id"]))
    asyncio.run(first_repository.close())

    restarted_repository = _open_repository(settings)
    restarted_service = CloudService(
        restarted_repository,
        settings,
        id_factory=_SequentialIds(),
    )
    restarted_executor = FakeCloudExecutor()
    restarted_worker = CloudWorker(
        restarted_repository,
        restarted_executor,
        replace(settings, worker_id="api-worker-after-restart"),
    )
    restarted_app = create_gateway_app(
        runtime_status={"channels": {}},
        cloud_api=CloudApiDependencies(
            restarted_service,
            restarted_repository,
            settings,
        ),
    )
    try:
        with TestClient(restarted_app) as restarted_client:
            restored_workspace = restarted_client.get(
                f"/api/v1/cloud/workspaces/{workspace['id']}"
            )
            restored_run = restarted_client.get(f"/api/v1/cloud/runs/{run['id']}")
            restored_events = restarted_client.get(
                f"/api/v1/cloud/runs/{run['id']}/events"
            )
            assert restored_workspace.status_code == 200
            assert restored_run.json()["state"] == "queued"
            assert [
                event["event_type"] for event in restored_events.json()["items"]
            ] == ["run.queued"]

            asyncio.run(restarted_worker.run_until_idle())
            terminal = restarted_client.get(f"/api/v1/cloud/runs/{run['id']}")
            assert terminal.json()["state"] == "succeeded"
    finally:
        asyncio.run(restarted_repository.close())
