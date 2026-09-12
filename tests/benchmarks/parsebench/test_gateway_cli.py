from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import httpx

from aworld.benchmarks.parsebench.contracts import DATASET_REVISION, SCORER_REVISION
from aworld_cli.parsebench_gateway import (
    DEFAULT_MODEL_PROFILE,
    GatewayRunManifest,
    ParseBenchGatewayClient,
    build_submit_payload,
    inspect_parsebench_package,
    reduce_gateway_batch_results,
    select_package_tasks,
)


def _task(task_id: str, dimensions: list[str]) -> bytes:
    payload = {
        "task_id": task_id,
        "dataset_revision": DATASET_REVISION,
        "scorer_revision": SCORER_REVISION,
        "dimensions": dimensions,
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        content = json.dumps(payload).encode()
        info = tarfile.TarInfo(f"{task_id}/tests/ground_truth.json")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _package(path: Path) -> Path:
    tasks = {
        "pb-table": ["table", "text_content"],
        "pb-chart": ["chart"],
        "pb-format": ["text_formatting"],
        "pb-layout": ["layout"],
    }
    catalog = b"".join(
        json.dumps(
            {"dataset_id": "parsebench-smoke-v1", "sample_id": task, "task_id": task}
        ).encode()
        + b"\n"
        for task in tasks
    )
    manifest = {
        "schema_version": "yolo-dataset-material-manifest/v1",
        "tasks": [
            {"task_id": task, "path": f"tasks/{task}.tar.gz"} for task in tasks
        ],
        "provenance": {
            "dataset_revision": DATASET_REVISION,
            "scorer_revision": SCORER_REVISION,
            "selection": {"kind": "balanced-smoke"},
        },
    }
    with ZipFile(path, "w", compression=ZIP_STORED) as archive:
        archive.writestr(
            "dataset.yaml",
            'schema_version: "yolo-dataset-package/v2"\n'
            'dataset_id: "parsebench-smoke-v1"\n'
            'service_name: "aworld-parsebench"\n',
        )
        archive.writestr("dataset.jsonl", catalog)
        archive.writestr("manifest.json", json.dumps(manifest))
        for task, dimensions in tasks.items():
            archive.writestr(f"tasks/{task}.tar.gz", _task(task, dimensions))
    return path


def _reward(dimension: str, score: float) -> dict[str, float | int]:
    prefix = f"parsebench_{dimension}"
    return {
        "reward": score,
        f"{prefix}_score": score,
        f"{prefix}_numeric_count": 1,
        f"{prefix}_not_scored_count": 0,
        f"{prefix}_official_failure_count": 0,
        f"{prefix}_execution_failure_count": 0,
        f"{prefix}_missing_count": 0,
    }


def test_package_submit_and_smoke_report_are_deterministic(tmp_path: Path) -> None:
    package = inspect_parsebench_package(_package(tmp_path / "dataset.zip"))
    selected = select_package_tasks(package)
    payload = build_submit_payload(package, selected_task_ids=selected)

    assert package.publishable is False
    assert payload["scheduler_config"] == {
        "execution_environment": "agent_only",
        "harness_profile": "aworld",
        "model_profile": DEFAULT_MODEL_PROFILE,
    }
    assert all(
        set(variant) == {"harness_profile", "model_id"}
        for variant in payload["payload"]["execution_variants"]
    )
    response = {
        "batch_id": "batch-1",
        "total": 4,
        "run_ids": ["run-1", "run-2", "run-3", "run-4"],
    }
    manifest = GatewayRunManifest.from_submission(
        package,
        selected_task_ids=selected,
        model_profile=DEFAULT_MODEL_PROFILE,
        response=response,
    )
    results = []
    by_task = {
        "pb-table": {**_reward("table", 0.5), **_reward("text_content", 0.7)},
        "pb-chart": _reward("chart", 0.6),
        "pb-format": _reward("text_formatting", 0.8),
        "pb-layout": _reward("layout", 0.9),
    }
    for task_id, run_id in manifest.task_to_run.items():
        results.append(
            {
                "run_id": run_id,
                "status": "COMPLETED",
                "data": {"status": "completed", "rewards": by_task[task_id]},
            }
        )
    report = reduce_gateway_batch_results(
        manifest,
        {"batch_id": "batch-1", "results": results},
    )

    assert report["status"] == "scored"
    assert report["overall_score"] == 0.7
    assert report["publishable"] is False
    assert "not leaderboard-publishable" in report["diagnostics"][-1]


def test_gateway_client_streams_package_and_paginates_without_secret_output(
    tmp_path: Path,
) -> None:
    package = inspect_parsebench_package(_package(tmp_path / "dataset.zip"))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer private-token"
        if request.url.path.endswith("/package/import"):
            assert request.headers["x-dataset-content-sha256"] == package.package_sha256
            assert request.read().startswith(b"PK")
            return httpx.Response(
                200,
                json={
                    "dataset_id": package.dataset_id,
                    "service_name": package.service_name,
                },
            )
        if request.url.path.endswith("/batch/submit"):
            return httpx.Response(
                202,
                json={"batch_id": "batch-1", "total": 1, "run_ids": ["run-1"]},
            )
        body = json.loads(request.read())
        if body["offset"] == 0:
            return httpx.Response(
                200,
                json={
                    "batch_id": "batch-1",
                    "status": "running",
                    "total": 2,
                    "completed": 1,
                    "failed": 0,
                    "results": [{"run_id": "run-1"}],
                    "has_more": True,
                },
            )
        return httpx.Response(
            200,
            json={
                "batch_id": "batch-1",
                "status": "running",
                "total": 2,
                "completed": 1,
                "failed": 0,
                "results": [{"run_id": "run-2"}],
                "has_more": False,
            },
        )

    with ParseBenchGatewayClient(
        "https://gateway.example/scheduler",
        token="private-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        client.import_package(package)
        client.submit({"scheduler_type": "harbor", "items": [{}]})
        result = client.batch_results("batch-1", page_size=1)

    assert [item["run_id"] for item in result["results"]] == ["run-1", "run-2"]
    assert "private-token" not in repr(client.__dict__)
