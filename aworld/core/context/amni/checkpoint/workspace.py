from typing import Optional, Dict, Any, List

from ..worksapces import Workspaces
from aworld.checkpoint import BaseCheckpointRepository, Checkpoint, VersionUtils
from aworld.output import Artifact, ArtifactType

CHECKPOINT_LATEST = "checkpoint_latest"


class CheckpointArtifact(Artifact):
    def __init__(self, content: Dict[str, Any], **kwargs):
        # Remove artifact_type from kwargs if it exists to avoid conflicts
        kwargs.pop('artifact_type', None)
        super().__init__(
            artifact_type=ArtifactType.JSON,
            content=content,
            **kwargs
        )


class WorkspaceCheckpointRepository(BaseCheckpointRepository):

    def __init__(self, workspaces: Workspaces):
        self._workspaces = workspaces

    def get(self, checkpoint_id: str) -> Optional[Checkpoint]:
        pass

    def list(self, params: Dict[str, Any]) -> List[Checkpoint]:
        pass

    def put(self, checkpoint: Checkpoint) -> None:
        pass

    def get_by_session(self, session_id: str) -> Optional[Checkpoint]:
        return self.aget_by_session(session_id)

    def delete_by_session(self, session_id: str) -> None:
        pass

    async def aget(self, checkpoint_id: str) -> Optional[Checkpoint]:
        return await super().aget(checkpoint_id)

    async def alist(self, params: Dict[str, Any]) -> List[Checkpoint]:
        return await super().alist(params)

    async def aput(self, checkpoint: Checkpoint) -> None:
        # Find last version checkpoint by session_id
        last_checkpoint = await self.aget_by_session(checkpoint.metadata.session_id)

        if last_checkpoint:
            # Compare versions to ensure optimistic locking
            if VersionUtils.is_version_less(checkpoint, last_checkpoint.version):
                raise ValueError(
                    f"New checkpoint version {checkpoint.version} must be greater than last version {last_checkpoint.version}")

        # Store the latest checkpoint
        checkpoint_artifact = CheckpointArtifact(
            artifact_id=CHECKPOINT_LATEST,
            content = checkpoint.model_dump()
        )
        version_artifact = CheckpointArtifact(
            artifact_id=f"checkpoint_{checkpoint.version}",
            content=checkpoint.model_dump()
        )

        # checkpoint reference the last version of artifact
        checkpoint.metadata.artifact_id = checkpoint_artifact.artifact_id
        checkpoint_artifact.content = checkpoint.model_dump()

        workspace = await self.get_session_workspace(checkpoint.metadata.session_id)
        await workspace.add_artifact(checkpoint_artifact, index=False)
        await workspace.add_artifact(version_artifact, index=False)


    async def aget_by_session(self, session_id: str) -> Optional[Checkpoint]:
        workspace = await self.get_session_workspace(session_id)
        checkpoint_artifact = workspace.get_artifact(CHECKPOINT_LATEST)
        if not checkpoint_artifact:
            return None
        return Checkpoint.model_validate(checkpoint_artifact.content)



    async def adelete_by_session(self, session_id: str) -> None:
        workspace = await self.get_session_workspace(session_id)
        await workspace.delete_artifact(CHECKPOINT_LATEST)

    async def get_session_workspace(self, session_id: str) -> "ApplicationWorkspace":
        return await self._workspaces.get_session_workspace(session_id)
