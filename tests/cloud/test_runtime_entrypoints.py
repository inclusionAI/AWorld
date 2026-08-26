from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aworld.cloud.healthcheck import main as healthcheck_main
from aworld.cloud.local_docker_executor import LocalDockerExecutorSettings
from aworld.cloud.runtime import cloud_settings_from_env
from aworld.cloud.server import create_cloud_app
from aworld.cloud.worker_main import verify_executor_ready


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "AWORLD_CLOUD_DATA_DIR": str(tmp_path / "cloud-data"),
        "AWORLD_CLOUD_PROFILE": "terminal-bench",
        "AWORLD_CLOUD_RUNTIME_IMAGE": "aworld-cloud-mvp:test",
        "AWORLD_CLOUD_RUNTIME_USER": "root",
        "AWORLD_CLOUD_RUN_NETWORK": "bridge",
        "AWORLD_CLOUD_WORKER_ID": "worker-test",
    }


def test_environment_settings_build_mvp_profile(tmp_path: Path) -> None:
    settings = cloud_settings_from_env(_environment(tmp_path))

    assert settings.enabled
    assert settings.database_path == tmp_path / "cloud-data" / "cloud.sqlite3"
    assert settings.worker_id == "worker-test"
    profile = settings.profile("terminal-bench")
    assert profile.runtime_image == "aworld-cloud-mvp:test"
    assert profile.runtime_user == "root"
    assert profile.network.mode == "bridge"


def test_server_entrypoint_initializes_sqlite_and_cloud_routes(tmp_path: Path) -> None:
    settings = cloud_settings_from_env(_environment(tmp_path))

    with TestClient(create_cloud_app(settings)) as client:
        health = client.get("/healthz")
        created = client.post(
            "/api/v1/cloud/workspaces",
            json={
                "name": "terminal-bench-smoke",
                "profile_name": "terminal-bench",
                "idempotency_key": "workspace-test",
            },
        )

    assert health.status_code == 200
    assert health.json() == {
        "ok": True,
        "service": "aworld-cloud-server",
        "storage": "sqlite",
    }
    assert created.status_code == 201
    assert created.json()["state"] == "ready"
    assert settings.database_path is not None
    assert settings.database_path.is_file()


def test_worker_healthcheck_rejects_stale_marker(tmp_path: Path) -> None:
    marker = tmp_path / "worker-health.json"
    marker.write_text(json.dumps({"ok": True}), encoding="utf-8")

    assert healthcheck_main(["worker", "--path", str(marker)]) == 0
    assert (
        healthcheck_main(
            [
                "worker",
                "--path",
                str(marker),
                "--max-age-seconds",
                "0.000001",
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_worker_preflight_checks_harbor_and_docker(tmp_path: Path) -> None:
    command = tmp_path / "dependency.py"
    command.write_text("raise SystemExit(0)\n", encoding="utf-8")
    settings = LocalDockerExecutorSettings(
        harbor_command=(sys.executable, str(command)),
        docker_command=(sys.executable, str(command)),
    )

    await verify_executor_ready(settings)
