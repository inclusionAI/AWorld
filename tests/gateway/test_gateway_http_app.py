from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.http.artifact_service import ArtifactService
from aworld_gateway.http.server import create_gateway_app


def test_gateway_http_app_exposes_channel_map_and_telegram_webhook() -> None:
    seen: dict[str, object] = {}

    class FakeAdapter:
        async def handle_update(self, payload: dict[str, object]) -> None:
            seen["payload"] = payload

    app = create_gateway_app(
        runtime_status={
            "channels": {
                "telegram": {
                    "enabled": False,
                    "configured": False,
                    "implemented": True,
                    "running": False,
                    "state": "registered",
                    "error": None,
                }
            }
        },
        telegram_adapter=FakeAdapter(),
    )

    assert app.title == "aworld-gateway"

    client = TestClient(app)

    assert client.get("/channels").json() == {
        "telegram": {
            "enabled": False,
            "configured": False,
            "implemented": True,
            "running": False,
            "state": "registered",
            "error": None,
        }
    }

    response = client.post(
        "/webhooks/telegram",
        json={
            "message": {
                "message_id": 1,
                "chat": {"id": 1001},
                "from": {"id": 7},
                "text": "hi",
            }
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert seen["payload"] == {
        "message": {
            "message_id": 1,
            "chat": {"id": 1001},
            "from": {"id": 7},
            "text": "hi",
        }
    }


def test_gateway_http_app_rejects_telegram_webhook_when_adapter_missing() -> None:
    app = create_gateway_app(
        runtime_status={"channels": {}},
        telegram_adapter=None,
    )

    client = TestClient(app)
    response = client.post("/webhooks/telegram", json={"message": {"text": "hi"}})

    assert response.status_code == 503
    assert response.json()["detail"] == "Telegram channel is not running."


def test_gateway_http_app_logs_telegram_webhook_requests(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: dict[str, object] = {}

    class FakeAdapter:
        async def handle_update(self, payload: dict[str, object]) -> None:
            seen["payload"] = payload

    app = create_gateway_app(
        runtime_status={"channels": {}},
        telegram_adapter=FakeAdapter(),
    )
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    client = TestClient(app)
    response = client.post("/webhooks/telegram", json={"message": {"text": "hi"}})

    assert response.status_code == 200
    assert seen["payload"] == {"message": {"text": "hi"}}
    assert "Telegram webhook received path=/webhooks/telegram" in caplog.text
    assert "Telegram webhook handled path=/webhooks/telegram" in caplog.text


def test_gateway_http_app_logs_telegram_webhook_when_adapter_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_gateway_app(
        runtime_status={"channels": {}},
        telegram_adapter=None,
    )
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    client = TestClient(app)
    response = client.post("/webhooks/telegram", json={"message": {"text": "hi"}})

    assert response.status_code == 503
    assert "Telegram webhook rejected path=/webhooks/telegram reason=adapter_missing" in caplog.text


def test_gateway_http_app_honors_custom_telegram_webhook_path() -> None:
    seen: dict[str, object] = {}

    class FakeAdapter:
        async def handle_update(self, payload: dict[str, object]) -> None:
            seen["payload"] = payload

    app = create_gateway_app(
        runtime_status={"channels": {}},
        telegram_adapter=FakeAdapter(),
        telegram_webhook_path="/hooks/custom-telegram",
    )

    client = TestClient(app)

    assert client.post("/webhooks/telegram", json={}).status_code == 404
    response = client.post("/hooks/custom-telegram", json={"message": {"text": "hi"}})

    assert response.status_code == 200
    assert seen["payload"] == {"message": {"text": "hi"}}


def test_gateway_http_app_reads_live_channel_status_from_provider() -> None:
    state = {
        "channels": {
            "telegram": {
                "enabled": False,
                "configured": False,
                "implemented": True,
                "running": False,
                "state": "registered",
                "error": None,
            }
        }
    }

    def runtime_status() -> dict[str, object]:
        return state

    app = create_gateway_app(runtime_status=runtime_status)
    client = TestClient(app)

    assert client.get("/channels").json()["telegram"]["running"] is False

    state["channels"]["telegram"]["running"] = True
    state["channels"]["telegram"]["state"] = "running"

    assert client.get("/channels").json()["telegram"]["running"] is True


def test_gateway_http_app_logs_healthz_and_channels_requests(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_gateway_app(
        runtime_status={
            "channels": {
                "telegram": {
                    "enabled": False,
                    "configured": False,
                    "implemented": True,
                    "running": False,
                    "state": "registered",
                    "error": None,
                }
            }
        }
    )
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    client = TestClient(app)
    health_response = client.get("/healthz")
    channels_response = client.get("/channels")

    assert health_response.status_code == 200
    assert channels_response.status_code == 200
    assert "Gateway HTTP request method=GET path=/healthz status=200" in caplog.text
    assert "Gateway HTTP request method=GET path=/channels status=200" in caplog.text


def test_gateway_http_app_logs_failing_requests(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_gateway_app(runtime_status={"channels": {}})

    @app.get("/boom")
    async def boom() -> dict[str, object]:
        raise RuntimeError("boom")

    caplog.set_level(logging.INFO, logger="aworld.gateway")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")

    assert response.status_code == 500
    assert "Gateway HTTP request failed method=GET path=/boom error=boom" in caplog.text


def test_gateway_http_app_serves_registered_artifact(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_root / "report.html"
    artifact_file.write_text("<html><body>report</body></html>", encoding="utf-8")

    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[artifact_root],
    )
    token = service.publish(artifact_file)
    app = create_gateway_app(runtime_status={"channels": {}}, artifact_service=service)

    client = TestClient(app)
    response = client.get(f"/artifacts/{token}")

    assert response.status_code == 200
    assert "report" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_gateway_http_app_logs_artifact_requests(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aworld.gateway")
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_root / "report.html"
    artifact_file.write_text("<html><body>report</body></html>", encoding="utf-8")

    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[artifact_root],
    )
    token = service.publish(artifact_file)
    app = create_gateway_app(runtime_status={"channels": {}}, artifact_service=service)

    client = TestClient(app)
    ok_response = client.get(f"/artifacts/{token}")
    missing_response = client.get("/artifacts/missing-token")

    assert ok_response.status_code == 200
    assert missing_response.status_code == 404
    assert f"Artifact published token={token}" in caplog.text
    assert f"Artifact request served token={token}" in caplog.text
    assert "Artifact request failed token=missing-token reason=not_found" in caplog.text


def test_gateway_http_app_rejects_unknown_artifact_token(tmp_path: Path) -> None:
    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[tmp_path],
    )
    app = create_gateway_app(runtime_status={"channels": {}}, artifact_service=service)

    client = TestClient(app)
    response = client.get("/artifacts/missing-token")

    assert response.status_code == 404
    assert response.json()["detail"] == "Artifact not found."


def test_gateway_http_app_rejects_requests_when_artifact_service_missing() -> None:
    app = create_gateway_app(runtime_status={"channels": {}}, artifact_service=None)

    client = TestClient(app)
    response = client.get("/artifacts/some-token")

    assert response.status_code == 503
    assert response.json()["detail"] == "Artifact service is not running."


def test_gateway_http_app_serves_snapshot_when_source_is_replaced_with_out_of_root_symlink(
    tmp_path: Path,
) -> None:
    escape_target = Path("/etc/hosts")
    if not escape_target.exists() or not escape_target.is_file():
        pytest.skip("/etc/hosts is not available on this machine")

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_root / "report.html"
    artifact_file.write_text("<html><body>report</body></html>", encoding="utf-8")

    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[artifact_root],
    )
    token = service.publish(artifact_file)

    artifact_file.unlink()
    try:
        artifact_file.symlink_to(escape_target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation unsupported: {exc}")

    app = create_gateway_app(runtime_status={"channels": {}}, artifact_service=service)
    client = TestClient(app)
    response = client.get(f"/artifacts/{token}")

    assert response.status_code == 200
    assert "report" in response.text


def test_gateway_http_app_serves_unicode_named_artifact(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_root / "名字.html"
    artifact_file.write_text("<html><body>report</body></html>", encoding="utf-8")

    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[artifact_root],
    )
    token = service.publish(artifact_file)

    app = create_gateway_app(runtime_status={"channels": {}}, artifact_service=service)
    client = TestClient(app)
    response = client.get(f"/artifacts/{token}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "report" in response.text


def test_gateway_http_app_serves_original_snapshot_after_source_rewrite(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_root / "report.html"
    artifact_file.write_text("<html><body>v1</body></html>", encoding="utf-8")

    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[artifact_root],
    )
    token = service.publish(artifact_file)

    artifact_file.write_text("<html><body>v2</body></html>", encoding="utf-8")

    app = create_gateway_app(runtime_status={"channels": {}}, artifact_service=service)
    client = TestClient(app)
    response = client.get(f"/artifacts/{token}")

    assert response.status_code == 200
    assert "v1" in response.text
    assert "v2" not in response.text


def test_gateway_http_app_returns_404_when_artifact_disappears_before_open(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_root / "report.html"
    artifact_file.write_text("<html><body>report</body></html>", encoding="utf-8")

    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[artifact_root],
    )
    token = service.publish(artifact_file)
    original_resolve = service.resolve

    def resolve_and_delete(resolved_token: str):  # type: ignore[no-untyped-def]
        artifact = original_resolve(resolved_token)
        if artifact is not None:
            artifact.path.unlink()
        return artifact

    service.resolve = resolve_and_delete  # type: ignore[assignment]

    app = create_gateway_app(runtime_status={"channels": {}}, artifact_service=service)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/artifacts/{token}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Artifact not found."


def test_artifact_service_build_external_url_requires_public_base_url(tmp_path: Path) -> None:
    service = ArtifactService(public_base_url=None, allowed_roots=[tmp_path])

    with pytest.raises(ValueError, match="Artifact public_base_url is not configured."):
        service.build_external_url("abc")


def test_artifact_service_logs_external_url_and_invalid_snapshot(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_root / "report.html"
    artifact_file.write_text("<html><body>v1</body></html>", encoding="utf-8")
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[artifact_root],
    )
    token = service.publish(artifact_file)
    url = service.build_external_url(token)
    artifact = service.resolve(token)
    assert artifact is not None
    artifact.path.unlink()
    resolved_again = service.resolve(token)

    assert url.endswith(f"/artifacts/{token}")
    assert resolved_again is None
    assert f"Artifact external url built token={token}" in caplog.text
    assert f"Artifact resolve invalid token={token} reason=missing_snapshot" in caplog.text


def test_gateway_http_app_serves_snapshot_after_source_delete(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_root / "report.html"
    artifact_file.write_text("<html><body>v1</body></html>", encoding="utf-8")

    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[artifact_root],
    )
    token = service.publish(artifact_file)

    artifact_file.unlink()

    app = create_gateway_app(runtime_status={"channels": {}}, artifact_service=service)
    client = TestClient(app)
    response = client.get(f"/artifacts/{token}")

    assert response.status_code == 200
    assert "v1" in response.text


def test_gateway_http_app_sanitizes_content_disposition_for_edge_case_filenames(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)

    quoted_name = 'a"b.html'
    quoted_file = artifact_root / quoted_name
    quoted_file.write_text("<html><body>quoted</body></html>", encoding="utf-8")

    newline_name = "line\nbreak.html"
    newline_file = artifact_root / newline_name
    newline_file.write_text("<html><body>newline</body></html>", encoding="utf-8")

    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[artifact_root],
    )
    quoted_token = service.publish(quoted_file)
    newline_token = service.publish(newline_file)

    app = create_gateway_app(runtime_status={"channels": {}}, artifact_service=service)
    client = TestClient(app)

    quoted_response = client.get(f"/artifacts/{quoted_token}")
    newline_response = client.get(f"/artifacts/{newline_token}")

    assert quoted_response.status_code == 200
    assert newline_response.status_code == 200

    quoted_disposition = quoted_response.headers["content-disposition"]
    newline_disposition = newline_response.headers["content-disposition"]

    assert "filename*=" in quoted_disposition
    assert "filename*=" in newline_disposition

    assert "\n" not in quoted_disposition
    assert "\r" not in quoted_disposition
    assert "\n" not in newline_disposition
    assert "\r" not in newline_disposition

    assert 'filename="a"b.html"' not in quoted_disposition
