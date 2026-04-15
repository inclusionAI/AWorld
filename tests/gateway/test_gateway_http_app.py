from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
