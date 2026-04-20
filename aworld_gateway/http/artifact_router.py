from __future__ import annotations

from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from aworld_gateway.http.artifact_service import ArtifactService


def _build_inline_content_disposition(filename: str) -> str:
    quoted_filename = quote(filename, safe="")
    return f"inline; filename*=UTF-8''{quoted_filename}"


def register_artifact_routes(app: FastAPI, artifact_service: ArtifactService | None = None) -> None:
    @app.get("/artifacts/{token}")
    async def get_artifact(token: str) -> StreamingResponse:
        if artifact_service is None:
            raise HTTPException(status_code=503, detail="Artifact service is not running.")

        artifact = artifact_service.resolve(token)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")

        try:
            artifact_stream = artifact.path.open("rb")
        except OSError:
            raise HTTPException(status_code=404, detail="Artifact not found.")

        return StreamingResponse(
            artifact_stream,
            media_type=artifact.content_type,
            headers={"content-disposition": _build_inline_content_disposition(artifact.path.name)},
            background=BackgroundTask(artifact_stream.close),
        )
