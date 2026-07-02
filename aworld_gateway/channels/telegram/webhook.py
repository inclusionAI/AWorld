from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request, status

from aworld_gateway.logging import get_gateway_logger

logger = get_gateway_logger("telegram.webhook")


def register_telegram_webhook(
    app: FastAPI,
    *,
    adapter: Any | None,
    path: str = "/webhooks/telegram",
) -> None:
    @app.post(path)
    async def telegram_webhook(request: Request) -> dict[str, object]:
        logger.info(f"Telegram webhook received path={request.url.path}")
        if adapter is None:
            logger.warning(
                f"Telegram webhook rejected path={request.url.path} reason=adapter_missing"
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telegram channel is not running.",
            )
        payload = await request.json()
        await adapter.handle_update(payload)
        logger.info(f"Telegram webhook handled path={request.url.path}")
        return {"ok": True}
