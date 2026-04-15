from __future__ import annotations

from fastapi import FastAPI


def create_gateway_app(*, runtime_status: dict[str, object]) -> FastAPI:
    app = FastAPI(title="Aworld Gateway", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True}

    @app.get("/channels")
    async def channels() -> dict[str, object]:
        return runtime_status

    return app
