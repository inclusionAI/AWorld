from __future__ import annotations

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
