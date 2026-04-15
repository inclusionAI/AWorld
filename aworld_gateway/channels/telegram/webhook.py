from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request


def register_telegram_webhook(
    app: FastAPI,
    *,
    adapter: Any | None,
    path: str = "/webhooks/telegram",
) -> None:
    @app.post(path)
    async def telegram_webhook(request: Request) -> dict[str, object]:
        payload = await request.json()
        if adapter is not None:
            await adapter.handle_update(payload)
        return {"ok": True}
