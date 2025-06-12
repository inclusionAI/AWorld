import logging
import os

from aworld.output import WorkSpace, ArtifactType
from fastapi import APIRouter, HTTPException, status

from config import WORKSPACE_PATH, WORKSPACE_TYPE

router = APIRouter()

@router.get("/{workspace_id}/tree")
async def get_workspace_tree(workspace_id: str):
    logging.info(f"get_workspace_tree: {workspace_id}")
    workspace = await load_workspace(workspace_id)
    return workspace.generate_tree_data()


@router.get("/{workspace_id}/artifacts")
async def get_workspace_artifacts(workspace_id: str, artifact_type: str):
    if artifact_type not in ArtifactType.__members__:
        logging.error(f"Invalid artifact_type: {artifact_type}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact type")

    logging.info(f"Fetching artifacts of type: {artifact_type}")
    workspace = await load_workspace(workspace_id)

    return {
        "data": workspace.list_artifacts(ArtifactType[artifact_type])
    }


@router.get("/{workspace_id}/file/{artifact_id}/content")
async def get_workspace_file_content(workspace_id: str, artifact_id: str):
    logging.info(f"get_workspace_file_content: {workspace_id}, {artifact_id}")
    workspace = await load_workspace(workspace_id)
    return {
        "data": workspace.get_file_content_by_artifact_id(artifact_id)
    }

    
def load_workspace(workspace_id: str):
    
    """
    This function is used to get the workspace by its id.
    It first checks the workspace type and then creates the workspace accordingly.
    If the workspace type is not valid, it raises an HTTPException.
    """
    if workspace_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")
    
    if WORKSPACE_TYPE == "local":
        workspace = WorkSpace.from_local_storages(workspace_id, storage_path=os.path.join(WORKSPACE_PATH, workspace_id))
    elif WORKSPACE_TYPE == "oss":
        workspace = WorkSpace.from_oss_storages(workspace_id, storage_path=os.path.join(WORKSPACE_PATH, workspace_id))
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid workspace type")
    return workspace