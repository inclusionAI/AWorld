import json
import traceback
import uuid
from typing import Optional, Any, Dict

from aworld.core.common import ActionResult
from aworld.core.context.amni import SearchArtifact
from aworld.core.context.amni.retrieval.artifacts.playwright import PlaywrightSnapshotArtifact
from aworld.logs.util import logger
from aworld.output import Artifact, ArtifactType, SearchOutput


def extract_artifacts_from_toolresult(tool_result: ActionResult) -> Optional[list[Artifact]]:
    artifacts = []
    try:
        artifact_meta = tool_result.metadata
        if artifact_meta and artifact_meta.get("offload", False) == True:
            artifact_data_list = json.loads(tool_result.content)
            logger.info(f"{tool_result.action_name} use SearchArtifact")
            for search_result in artifact_data_list:
                search_result = json.loads(search_result)
                if isinstance(search_result, list):
                    for item in search_result:
                        artifacts.append(
                            SearchArtifact.create(query=artifact_meta.get('query'),
                                                  title=item.get('title'),
                                                  url=item.get('url'),
                                                  content=item.get('content'),
                                                  metadata={
                                                      "origin_tool_name": tool_result.tool_name,
                                                      "origin_action_name": tool_result.action_name,
                                                  }))
                elif isinstance(search_result, dict):
                    artifacts.append(
                        SearchArtifact.create(query=artifact_meta.get('query'),
                                              title=search_result.get('title'),
                                              url=search_result.get('url'),
                                              content=search_result.get('content'),
                                              metadata={
                                                  "origin_tool_name": tool_result.tool_name,
                                                  "origin_action_name": tool_result.action_name,
                                              }))
        elif tool_result.action_name.startswith('browser'):
            logger.info(f"{tool_result.action_name} use PlaywrightSnapshotArtifact")
            content = json.loads(tool_result.content)[0]
            artifact = PlaywrightSnapshotArtifact.create(url="",
                                                         content=content,
                                                         metadata={
                                                             "origin_tool_name": tool_result.tool_name,
                                                             "origin_action_name": tool_result.action_name,
                                                         })
            artifacts.append(artifact)
        else:
            # 默认处理
            logger.info(f"{tool_result.action_name} use Default Artifact")
            content = json.loads(tool_result.content)[0]
            artifact = Artifact(artifact_id=str(uuid.uuid4()),
                                artifact_type=ArtifactType.TEXT,
                                content=content,
                                metadata={
                                    "origin_tool_name": tool_result.tool_name,
                                    "origin_action_name": tool_result.action_name,
                                })
            artifacts.append(artifact)
    except Exception as e:
        logger.warning(f"extract_artifacts_from_toolresult, Exception is {e}\n,  toolresult is {tool_result}\n trace is {traceback.format_exc()}")
    return artifacts

def extract_artifacts_from_toolresult_metadata(metadata: Dict[str, Any]) -> Optional[list[Artifact]]:
    try:
        if not metadata:
            logger.info("tool_result.metadata is empty, not process")
            return None
        artifacts = []
        # screenshots
        if metadata.get('screenshots') and isinstance(metadata.get('screenshots'), list) and len(
                metadata.get('screenshots')) > 0:
            for index, screenshot in enumerate(metadata.get('screenshots')):
                image_artifact = Artifact(artifact_id=str(uuid.uuid4()), artifact_type=ArtifactType.IMAGE,
                                          content=screenshot.get('ossPath'))
                artifacts.append(image_artifact)

        # common artifact
        if metadata.get("artifacts") and isinstance(metadata.get("artifacts"), list):
            for item in metadata.get("artifacts"):
                artifacts.append(build_artifact(item))
        elif metadata.get('artifact_type') and metadata.get('artifact_data'):
            artifacts.append(build_artifact({
                "artifact_type": metadata.get('artifact_type'),
                "artifact_data": metadata.get('artifact_data')
            }))
        return artifacts
    except Exception as err:
        logger.warning(f"extract_artifacts_form_toolresult_metadata, Exception is {err}\n,  metadata is {metadata}\n trace is {traceback.format_exc()}")

def build_artifact(artifact_item_data: dict, **kwargs) -> Optional[Artifact]:
    """
    Build an Artifact object based on artifact item data
    
    Args:
        artifact_item_data (dict): Dictionary containing artifact information
        **kwargs: Additional keyword arguments
        
    Returns:
        Optional[Artifact]: Constructed Artifact object or None if invalid
    """
    artifact_type = artifact_item_data.get("artifact_type")
    if artifact_type == 'WEB_PAGES':
        artifact_id = str(uuid.uuid4())
        data_dict = artifact_item_data.get("artifact_data")
        search_output = SearchOutput.from_dict(data_dict)
        return Artifact(
            artifact_type=ArtifactType.WEB_PAGES,
            artifact_id=artifact_id,
            content=search_output,
            metadata={
                "query": search_output.query,
            })
    elif artifact_type in ["MARKDOWN", "TEXT"]:
        artifact_id = str(uuid.uuid4())
        return Artifact(
            artifact_type=ArtifactType[artifact_type],
            artifact_id=artifact_id,
            content=artifact_item_data.get("artifact_data"),
            metadata={
            }
        )
    return None
