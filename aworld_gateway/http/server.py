from __future__ import annotations

from fastapi import FastAPI

from aworld_gateway.channels.telegram.webhook import register_telegram_webhook


def create_gateway_app(
    *,
    runtime_status: dict[str, object],
    telegram_adapter: object | None = None,
) -> FastAPI:
    app = FastAPI(title="Aworld Gateway", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True}

    @app.get("/channels")
    async def channels() -> dict[str, object]:
        channels_payload = runtime_status.get("channels")
        if isinstance(channels_payload, dict):
            return channels_payload
        return {}

    register_telegram_webhook(app, adapter=telegram_adapter)

    return app
