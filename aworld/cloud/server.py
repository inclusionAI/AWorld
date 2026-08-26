"""Runnable FastAPI composition root for the AWorld Cloud MVP server."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from aworld.cloud.runtime import cloud_settings_from_env
from aworld.cloud.service import CloudService
from aworld.cloud.settings import CloudSettings
from aworld.cloud.sqlite_repository import SQLiteCloudRepository
from aworld_gateway.http.cloud_router import CloudApiDependencies, register_cloud_routes


def create_cloud_app(settings: CloudSettings | None = None) -> FastAPI:
    """Build an isolated Cloud API app backed by the configured repository."""

    resolved = settings or cloud_settings_from_env()
    assert resolved.database_path is not None
    repository = SQLiteCloudRepository(resolved.database_path)
    service = CloudService(repository, resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await repository.initialize()
        app.state.cloud_ready = True
        try:
            yield
        finally:
            app.state.cloud_ready = False
            await repository.close()

    app = FastAPI(
        title="AWorld Cloud Server",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "ok": bool(getattr(app.state, "cloud_ready", False)),
            "service": "aworld-cloud-server",
            "storage": "sqlite",
        }

    register_cloud_routes(
        app,
        CloudApiDependencies(
            service=service,
            repository=repository,
            settings=resolved,
        ),
    )
    return app


app = create_cloud_app()
