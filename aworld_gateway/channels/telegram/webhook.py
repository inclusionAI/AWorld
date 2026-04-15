from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request, status


def register_telegram_webhook(
    app: FastAPI,
    *,
    adapter: Any | None,
    path: str = "/webhooks/telegram",
) -> None:
    @app.post(path)
    async def telegram_webhook(request: Request) -> dict[str, object]:
        if adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telegram channel is not running.",
            )
        payload = await request.json()
        await adapter.handle_update(payload)
        return {"ok": True}
