import os
import time
from typing import List, Optional
from pydantic import BaseModel

from fastapi import APIRouter, HTTPException, status, Query, Body

from aworld.logs.util import logger
from aworld.output import WorkSpace, ArtifactType
from aworld.output.utils import load_workspace

router = APIRouter()

prefix = "/api/workspaces"

@router.get("/{workspace_id}/tree")
async def get_workspace_tree(workspace_id: str):
    logger.info(f"get_workspace_tree: {workspace_id}")
    workspace = await get_workspace(workspace_id)
    return workspace.generate_tree_data()


class ArtifactRequest(BaseModel):
    artifact_ids: Optional[List[str]] = None
    artifact_types: Optional[List[str]] = None


@router.post("/{workspace_id}/artifacts")
async def get_workspace_artifacts(workspace_id: str, request: ArtifactRequest):
    """
    Get artifacts by workspace id and filter by a list of artifact types.
    Args:
        workspace_id: Workspace ID
        request: Request body containing optional artifact_types list
    Returns:
        Dict with filtered artifacts
    """
    artifact_types = request.artifact_types

    workspace = await get_workspace(workspace_id)
    all_artifacts = workspace.list_artifacts()
    start = time.perf_counter()
    filtered_artifacts = all_artifacts
    if request.artifact_ids:
        filtered_artifacts = [a for a in filtered_artifacts if a.artifact_id in request.artifact_ids]
    if artifact_types:
        filtered_artifacts = [a for a in filtered_artifacts if a.artifact_type.name in artifact_types]

    logger.info(f"get_workspace_artifacts: {workspace_id}, {artifact_types}, {len(filtered_artifacts)} artifacts, {time.perf_counter() - start:.4f}s")
    return {
        "data": [workspace.get_artifact_data(a.artifact_id) for a in filtered_artifacts]
    }


@router.get("/{workspace_id}/file/{artifact_id}/content")
async def get_workspace_file_content(workspace_id: str, artifact_id: str):
    logger.info(f"get_workspace_file_content: {workspace_id}, {artifact_id}")
    workspace = await get_workspace(workspace_id)
    return {
        "data": workspace.get_file_content_by_artifact_id(artifact_id)
    }


async def get_workspace(workspace_id: str) -> WorkSpace:
    workspace_type = os.environ.get("WORKSPACE_TYPE", "local")
    workspace_path = os.environ.get("WORKSPACE_PATH", "./data/workspaces")
    return await load_workspace(workspace_id, workspace_type, workspace_path, load_artifact_content=False)
