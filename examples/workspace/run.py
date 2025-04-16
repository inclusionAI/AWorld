import asyncio

from aworld.output import WorkSpace, ArtifactType
from aworld.output.observer import on_artifact_create, get_observer
import logging
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

@on_artifact_create
async def handle_artifact_create(artifact):
    logging.info(f"Artifact created: {artifact.artifact_id}")


@on_artifact_create(workspace_id="demo", filters={"artifact_type": "text"})
async def handle_specific_artifacts(artifact, **kwargs):
    logging.info(f"text artifact created in specific workspace {kwargs['workspace_id']}: {artifact.artifact_id}")


class DemoClass:
    def __init__(self):
        observer = get_observer()
        observer.register_create_handler(
            self.artifact_create,
            instance=self,
            workspace_id="demo"
        )

    async def artifact_create(self, artifact, **kwargs):
        logging.info(f"DemoClass : text artifact created in specific workspace {kwargs['workspace_id']}: {artifact.artifact_id}")


if __name__ == '__main__':
    DemoClass()
    workspace = WorkSpace.from_local_storages(workspace_id="demo")
    asyncio.run(workspace.create_artifact(ArtifactType.TEXT, "artifact_001"))