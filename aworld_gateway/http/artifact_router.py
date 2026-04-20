from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from aworld_gateway.http.artifact_service import ArtifactService


def register_artifact_routes(app: FastAPI, artifact_service: ArtifactService | None = None) -> None:
    @app.get("/artifacts/{token}")
    async def get_artifact(token: str) -> FileResponse:
        if artifact_service is None:
            raise HTTPException(status_code=503, detail="Artifact service is not running.")

        artifact = artifact_service.resolve(token)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")

        return FileResponse(
            path=artifact.path,
            media_type=artifact.content_type,
            headers={"content-disposition": f'inline; filename="{artifact.path.name}"'},
        )
