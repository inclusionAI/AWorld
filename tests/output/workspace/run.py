import asyncio
from dotenv import load_dotenv

from aworld.logs.util import logger
from aworld.output import WorkSpace, ArtifactType
from aworld.output.observer import on_artifact_create, get_observer


@on_artifact_create
async def handle_artifact_create(artifact):
    logger.info(f"Artifact created: {artifact.artifact_id}")


@on_artifact_create(workspace_id="demo", filters={"artifact_type": "text"})
async def handle_specific_artifacts(artifact, **kwargs):
    logger.info(f"text artifact created in specific workspace {kwargs['workspace_id']}: {artifact.artifact_id}")


class DemoClass:
    def __init__(self):
        observer = get_observer()
        observer.register_create_handler(
            self.artifact_create,
            instance=self,
            workspace_id="demo"
        )

    async def artifact_create(self, artifact, **kwargs):
        logger.info(f"DemoClass : text artifact created in specific workspace {kwargs['workspace_id']}: {artifact.artifact_id}")

async def run():
    load_dotenv()
    DemoClass()

    workspace = WorkSpace.from_local_storages(workspace_id="demo")
    await workspace.create_artifact(ArtifactType.TEXT, "artifact_001", content="123")
    await workspace.create_artifact(ArtifactType.TEXT, "artifact_001", content="456")
    await workspace.update_artifact("artifact_001", content="7890")
    await workspace.mark_as_completed("artifact_001")
    # await workspace.delete_artifact("artifact_001")

if __name__ == '__main__':
    asyncio.run(run())
