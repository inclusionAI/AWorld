from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from starlette.requests import Request

from aworld_gateway import GATEWAY_DISPLAY_NAME
from aworld_gateway.channels.telegram.webhook import register_telegram_webhook
from aworld_gateway.http.artifact_router import register_artifact_routes
from aworld_gateway.http.artifact_service import ArtifactService
from aworld_gateway.logging import get_gateway_logger

logger = get_gateway_logger("http.server")


def create_gateway_app(
    *,
    runtime_status: dict[str, object] | Callable[[], dict[str, object]],
    artifact_service: ArtifactService | None = None,
    telegram_adapter: object | None = None,
    telegram_webhook_path: str = "/webhooks/telegram",
) -> FastAPI:
    app = FastAPI(title=GATEWAY_DISPLAY_NAME, version="0.1.0")

    @app.middleware("http")
    async def log_http_request(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "Gateway HTTP request failed "
                f"method={request.method} path={request.url.path} error={exc}"
            )
            raise
        logger.info(
            "Gateway HTTP request "
            f"method={request.method} path={request.url.path} status={response.status_code}"
        )
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True}

    @app.get("/channels")
    async def channels() -> dict[str, object]:
        status_payload = runtime_status() if callable(runtime_status) else runtime_status
        channels_payload = status_payload.get("channels")
        if isinstance(channels_payload, dict):
            return channels_payload
        return {}

    register_artifact_routes(app, artifact_service=artifact_service)

    register_telegram_webhook(
        app,
        adapter=telegram_adapter,
        path=telegram_webhook_path,
    )

    return app
