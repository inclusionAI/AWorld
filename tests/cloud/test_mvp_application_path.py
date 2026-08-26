from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from aworld.cloud.local_docker_executor import (
    LocalDockerExecutorProvider,
    LocalDockerExecutorSettings,
)
from aworld.cloud.runtime import cloud_settings_from_env
from aworld.cloud.server import create_cloud_app
from aworld.cloud.sqlite_repository import SQLiteCloudRepository
from aworld.cloud.worker import CloudWorker


@pytest.mark.asyncio
async def test_http_to_worker_harbor_contract_and_atif_download(tmp_path: Path) -> None:
    """Exercise the process seam with a controlled CLI, not a fake executor."""

    harbor = tmp_path / "controlled_harbor.py"
    harbor.write_text(
        """
import json
import sys
from pathlib import Path

args = sys.argv[1:]
jobs_dir = Path(args[args.index("--jobs-dir") + 1])
job_name = args[args.index("--job-name") + 1]
job_dir = jobs_dir / job_name
trial_dir = job_dir / "fix-git__oracle__1"
trial_dir.mkdir(parents=True)
trial = {
    "task_name": "fix-git",
    "trial_name": "fix-git__oracle__1",
    "verifier_result": {"rewards": {"reward": 1}},
}
(trial_dir / "result.json").write_text(json.dumps(trial))
(job_dir / "result.json").write_text(json.dumps({"trial_results": [trial]}))
print("harbor contract output")
""".strip()
        + "\n",
        encoding="utf-8",
    )
    settings = cloud_settings_from_env(
        {
            "AWORLD_CLOUD_DATA_DIR": str(tmp_path / "data"),
            "AWORLD_CLOUD_PROFILE": "terminal-bench",
            "AWORLD_CLOUD_RUNTIME_IMAGE": "aworld-cloud-mvp:test",
            "AWORLD_CLOUD_RUNTIME_USER": "root",
            "AWORLD_CLOUD_RUN_NETWORK": "bridge",
            "AWORLD_CLOUD_WORKER_ID": "e2e-worker",
        }
    )
    assert settings.database_path is not None
    worker_repository = SQLiteCloudRepository(settings.database_path)
    await worker_repository.initialize()
    worker = CloudWorker(
        worker_repository,
        LocalDockerExecutorProvider(
            LocalDockerExecutorSettings(
                harbor_command=(sys.executable, str(harbor)),
            )
        ),
        settings,
    )
    app = create_cloud_app(settings)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://cloud.test",
        ) as client,
    ):
        workspace_response = await client.post(
            "/api/v1/cloud/workspaces",
            json={
                "name": "terminal-bench-smoke",
                "profile_name": "terminal-bench",
                "idempotency_key": "workspace-e2e",
            },
        )
        workspace_id = workspace_response.json()["id"]
        submit_response = await client.post(
            f"/api/v1/cloud/workspaces/{workspace_id}/runs",
            json={
                "idempotency_key": "run-e2e",
                "mode": "benchmark",
                "task": "Run the real Terminal-Bench fix-git task.",
                "benchmark": {
                    "dataset": "terminal-bench@2.0",
                    "task_id": "fix-git",
                    "harness": "harbor",
                },
            },
        )
        assert submit_response.status_code == 201
        run_id = submit_response.json()["id"]

        await worker.run_until_idle()

        run_response = await client.get(f"/api/v1/cloud/runs/{run_id}")
        run = run_response.json()
        assert run["state"] == "succeeded"
        assert run["benchmark_outcome"]["reward"] == 1.0
        events_response = await client.get(f"/api/v1/cloud/runs/{run_id}/events")
        assert any(
            event["event_type"] == "harbor.completed"
            for event in events_response.json()["items"]
        )
        files_response = await client.get(f"/api/v1/cloud/runs/{run_id}/files")
        files = files_response.json()["items"]
        assert {item["kind"] for item in files} >= {
            "stdout",
            "stderr",
            "result",
            "trajectory",
        }
        trajectory_id = run["canonical_trajectory_file_id"]
        trajectory_response = await client.get(
            f"/api/v1/cloud/runs/{run_id}/files/{trajectory_id}"
        )
        trajectory = trajectory_response.json()
        assert trajectory["schema_version"] == "ATIF-v1.7"
        assert trajectory["final_metrics"]["reward"] == 1.0

    await worker_repository.close()
