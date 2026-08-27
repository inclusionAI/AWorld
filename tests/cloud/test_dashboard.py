from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from aworld.cloud.runtime import cloud_settings_from_env
from aworld.cloud.server import create_cloud_app
from aworld.cloud.settings import CloudSettings


def _settings(tmp_path: Path) -> CloudSettings:
    return cloud_settings_from_env(
        {
            "AWORLD_CLOUD_DATA_DIR": str(tmp_path / "cloud-data"),
            "AWORLD_CLOUD_PROFILE": "terminal-bench",
            "AWORLD_CLOUD_RUNTIME_IMAGE": "aworld-cloud-mvp:test",
            "AWORLD_CLOUD_RUNTIME_USER": "root",
            "AWORLD_CLOUD_RUN_NETWORK": "bridge",
        }
    )


def test_dashboard_root_serves_dependency_free_operations_ui(tmp_path: Path) -> None:
    with TestClient(create_cloud_app(_settings(tmp_path))) as client:
        response = client.get("/")
        runs = client.get("/api/v1/cloud/runs")
        schema = client.get("/openapi.json").json()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert runs.status_code == 200
    assert runs.json()["items"] == []
    assert "/" not in schema["paths"]
    assert "/dashboard/health" not in schema["paths"]

    html = response.text
    for content in (
        "AWorld Cloud",
        "Server",
        "Worker",
        "Recent runs",
        "Auto refresh",
        "Run details",
        "Events",
        "Files",
        "Preview canonical ATIF",
        "Download",
        "Loading preview",
        "Could not preview file",
        "This file is empty",
        "Binary preview is not available",
        "Preview limited to the first",
        "Loading runs",
        "No runs yet",
        "Could not load runs",
    ):
        assert content in html
    assert 'const API = "/api/v1/cloud"' in html
    assert "fetchJSON(`${API}/runs?limit=100`)" in html
    assert "fetchJSON(`${API}/runs/${encoded}/events?limit=1000`)" in html
    assert "fetchJSON(`${API}/runs/${encoded}/files`)" in html
    assert "fetch(validDownload(file, run.id)" in html
    assert "`${API}/runs/${encodeURIComponent(runId)}/files/`" in html
    assert "const PREVIEW_BYTES = 256 * 1024" in html
    assert "Range: `bytes=0-${PREVIEW_BYTES - 1}`" in html
    assert "response.arrayBuffer()" in html
    assert 'new TextDecoder("utf-8", {fatal: false})' in html
    assert "JSON.stringify(JSON.parse(text), null, 2)" in html
    assert "content.textContent = previewState.text" in html
    assert 'element("button", "file-preview-button", file.relative_path)' in html
    assert 'fetchJSON("/dashboard/health")' in html
    assert "window.setInterval" in html
    assert "--blue: #175cd3" in html
    assert "@media (max-width: 680px)" in html
    assert "https://" not in html
    assert "http://" not in html


def test_dashboard_health_reports_live_worker_heartbeat(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    health_path = settings.data_root / "worker-health.json"
    with TestClient(create_cloud_app(settings)) as client:
        missing = client.get("/dashboard/health")
        health_path.write_text("[]", encoding="utf-8")
        malformed = client.get("/dashboard/health")
        health_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        healthy = client.get("/dashboard/health")
        old_timestamp = time.time() - 30
        os.utime(health_path, (old_timestamp, old_timestamp))
        stale = client.get("/dashboard/health")

    assert missing.status_code == 200
    assert missing.json()["server"]["ok"] is True
    assert missing.json()["worker"] == {
        "ok": False,
        "status": "Unavailable",
        "detail": "Worker heartbeat has not been observed",
        "age_seconds": None,
        "updated_at": None,
    }
    assert malformed.json()["worker"]["status"] == "Unavailable"
    assert healthy.json()["worker"]["ok"] is True
    assert healthy.json()["worker"]["status"] == "Healthy"
    assert healthy.json()["worker"]["updated_at"]
    assert stale.json()["worker"]["ok"] is False
    assert stale.json()["worker"]["status"] == "Stale"
