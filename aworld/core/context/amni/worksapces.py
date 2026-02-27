import asyncio
import logging
import os
import traceback
from typing import Optional, List

from aworld.output import WorkSpace, Artifact, WorkspaceObserver, ArtifactRepository


class ApplicationWorkspace(WorkSpace):

    def __init__(self, workspace_id: Optional[str] = None, name: Optional[str] = None,
                 storage_path: Optional[str] = None, observers: Optional[List[WorkspaceObserver]] = None,
                 use_default_observer: bool = True, clear_existing: bool = False,
                 repository: Optional[ArtifactRepository] = None, **kwargs):
        super().__init__(workspace_id, name, storage_path, observers, use_default_observer, clear_existing, repository,
                         **kwargs)
        # Retrieval / chunker is no longer used; keep workspace as a simple artifact store.

    async def add_artifact(self, artifact: Artifact, index: bool = False, **kwargs) -> None:
        """
        Add artifact to workspace.

        Note:
            Retrieval / chunk index is no longer maintained; this only persists artifact itself.
        """
        try:
            self._load_workspace_data(load_artifact_content=False)
            await super().add_artifact(artifact)
            logging.debug(f"add_artifact#{artifact.artifact_id} finished")
        except Exception as err:
            logging.warning(f"add_artifact Error is {err}, trace is {traceback.format_exc()}")

    async def read_artifact_content(self, artifact_id: str, start_line: int = 0, end_line: int = 10) -> Optional[str]:
        return self.get_artifact(artifact_id).content

    async def query_artifacts(self, search_filter: dict =None) -> Optional[list[Artifact]]:
        results = []
        if not search_filter:
            return None

        for artifact in self.artifacts:
            if search_filter:
                if all(key in artifact.metadata and artifact.metadata[key] == value for key, value in search_filter.items()):
                    results.append(artifact)
            else:
                results.append(artifact)
        return results


    """  Artifact Chunk CRUD (removed - retrieval no longer supported) """


async def load_workspace(workspace_id: str, workspace_type: str = None, workspace_parent_path: str = None) -> Optional[
    ApplicationWorkspace]:
    """
    This function is used to get the workspace by its id.
    It first checks the workspace type and then creates the workspace accordingly.
    If the workspace type is not valid, it raises an HTTPException.
    """
    if workspace_id is None:
        raise RuntimeError("workspace_id is None")
    if workspace_type is None:
        workspace_type = os.environ.get("WORKSPACE_TYPE", "local")
    if workspace_parent_path is None:
        workspace_parent_path = os.environ.get("WORKSPACE_PATH", "./data/workspaces")

    if workspace_type == "local":
        workspace = ApplicationWorkspace.from_local_storages(
            workspace_id,
            storage_path=os.path.join(workspace_parent_path,workspace_id),
        )
    elif workspace_type == "oss":
        workspace = ApplicationWorkspace.from_oss_storages(
            workspace_id,
            storage_path=os.path.join(workspace_parent_path, workspace_id),
        )
    else:
        raise RuntimeError("Invalid workspace type")
    return workspace


class Workspaces:
    """
    This class is used to get the workspace by its id.
    """
    async def get_session_workspace(self, session_id: str) -> ApplicationWorkspace:
        return await load_workspace(session_id)


workspace_repo = Workspaces()

