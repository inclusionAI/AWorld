# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List

from aworld.core.storage.base import Storage
from aworld.core.storage.file_store import FileStorage, FileConfig
from aworld.core.storage.oss_store import OssStorage, OssConfig
from aworld.core.storage.data import Data
from aworld.output.artifact import Artifact
from aworld.logs.util import logger
from aworld.utils.common import sync_exec


class ArtifactRepository:
    def __init__(self, storage_path: str, storage: Storage = FileStorage()):
        self.workspace_path = storage_path.rstrip('/')
        storage.conf.record_value_only = True
        self.storage = storage
        self.index_path = f"index.json"
        self.index = self.load_index()

    @staticmethod
    def create_local(storage_path: str) -> "ArtifactRepository":
        storage = FileStorage(FileConfig(work_dir=storage_path, record_value_only=True))
        return ArtifactRepository(storage_path=storage_path, storage=storage)

    @staticmethod
    def create_oss(access_key_id: str,
                   access_key_secret: str,
                   endpoint: str,
                   bucket_name: str,
                   storage_path: str = "aworld/workspaces/") -> "ArtifactRepository":
        storage = OssStorage(conf=OssConfig(
            access_id=access_key_id,
            access_key=access_key_secret,
            endpoint=endpoint,
            bucket=bucket_name,
            work_dir=storage_path,
            record_value_only=True
        ))
        return ArtifactRepository(storage_path=storage_path, storage=storage)

    def load_index(self) -> Dict[str, Any]:
        data = sync_exec(self.storage.get_data, self.workspace_path, self.index_path)
        if data:
            try:
                content = data[0].value if hasattr(data[0], 'value') else data[0]
                if isinstance(content, str):
                    content = json.loads(content)
                return content or {"artifacts": [], "versions": []}
            except Exception as e:
                logger.warning(f"Failed to load index file: {e}")
                return {"artifacts": [], "versions": []}
        else:
            index = {"artifacts": [], "versions": []}
            self.save_index(index)
            return index

    def save_index(self, index: Dict[str, Any]):
        sync_exec(self.storage.add_data, index, self.index_path, self.workspace_path)

    def _artifact_block_id(self, artifact_id: str) -> str:
        return f"{self.workspace_path}/{artifact_id}"

    def _attachment_block_id(self, artifact_id: str) -> str:
        return f"{self.workspace_path}/{artifact_id}/attachments"

    async def store_artifact(self, artifact: Artifact) -> str:
        """Store artifact and return its version identifier.

        Args:
            artifact: Artifact to be stored

        Returns:
            artifact_id
        """
        version = {
            "hash": artifact.artifact_id,
            "timestamp": time.time(),
            "metadata": artifact.metadata or {}
        }
        try:
            artifact_data = artifact.to_dict()
            # all name is index.json
            data = Data(
                id="index.json",
                block_id=self._artifact_block_id(artifact.artifact_id),
                value=artifact_data,
                meta_info={
                    "artifact_type": artifact.artifact_type.value,
                    "created_at": artifact.created_at,
                    "updated_at": artifact.updated_at,
                    "status": artifact.status.name
                }
            )
            await self.storage.create_data(data, overwrite=True)

            if artifact.attachments and artifact.need_save_attachment():
                await self._store_attachments(artifact)

            # Update index
            artifact_exists = False
            for item in self.index["artifacts"]:
                if item['artifact_id'] == artifact.artifact_id:
                    item['version'] = version
                    artifact_exists = True
                    break
            if not artifact_exists:
                self.index["artifacts"].append({
                    'artifact_id': artifact.artifact_id,
                    'type': 'artifact',
                    'version': version
                })
            self.save_index(self.index)
            return "success"
        except Exception as e:
            logger.error(f"Failed to store artifact {artifact.artifact_id}: {e}")
            raise

    async def _store_attachments(self, artifact: Artifact):
        block_id = self._attachment_block_id(artifact.artifact_id)

        for attachment in artifact.attachments:
            attachment_data = Data(
                id=attachment.filename,
                block_id=block_id,
                value={
                    "filename": attachment.filename,
                    "content": attachment.content if isinstance(attachment.content, str) else str(attachment.content),
                    "mime_type": attachment.mime_type,
                    "path": attachment.path,
                    "metadata": attachment.metadata
                },
                meta_info={
                    "artifact_id": artifact.artifact_id,
                    "mime_type": attachment.mime_type
                }
            )

            await self.storage.create_data(attachment_data, overwrite=True)

    def retrieve_latest_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        try:
            block_id = self._artifact_block_id(artifact_id)
            item = sync_exec(self.storage.get_data, block_id, self.index_path)
            item = item[0] if item else None
            content = item.value if hasattr(item, 'value') else item
            if isinstance(content, str):
                content = json.loads(content)
            return content
        except Exception as e:
            logger.error(f"Failed to retrieve artifact {artifact_id}: {e}")
        return None

    async def delete_artifact(self, artifact_id: str) -> bool:
        try:
            artifact_block = self._artifact_block_id(artifact_id)
            await self.storage.delete_block(artifact_block, exists=False)

            attachment_block = self._attachment_block_id(artifact_id)
            await self.storage.delete_block(attachment_block, exists=False)

            logger.info(f"Artifact deleted: {artifact_id}")
            # Remove from index
            for i, artifact in enumerate(self.index["artifacts"]):
                if artifact['artifact_id'] == artifact_id:
                    del self.index["artifacts"][i]
                    self.save_index(self.index)
                    return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete artifact {artifact_id}: {e}")
            return False

    def get_artifact_versions(self, artifact_id: str) -> List[Dict[str, Any]]:
        try:
            for artifact in self.index["artifacts"]:
                if artifact['artifact_id'] == artifact_id:
                    version_info = artifact["version"].copy()
                    version_info["artifact_id"] = artifact_id
                    return [version_info]
            return []
        except Exception as e:
            logger.warning(f"Failed to get version information: {e}")
            return []

    async def generate_tree_data(self, workspace_name: str) -> dict:
        default_res = {
            "name": workspace_name,
            "id": "-1",
            "type": "dir",
            "parentId": None,
            "depth": 0,
            "children": []
        }
        if isinstance(self.storage, FileStorage):
            return await self._generate_local_tree(workspace_name, default_res)
        elif isinstance(self.storage, OssStorage):
            return await self._generate_oss_tree(workspace_name, default_res)
        else:
            return default_res

    async def _generate_local_tree(self, workspace_name: str, default_res: dict) -> dict:
        root_path = Path(self.workspace_path)

        def build_tree(path: Path, parent_id: str, depth: int = 1) -> dict:
            node = {
                "name": path.name or workspace_name,
                "id": str(uuid.uuid4()),
                "type": "dir" if path.is_dir() else "file",
                "parentId": parent_id,
                "depth": depth,
                "expanded": False,
                "children": []
            }

            if path.is_dir():
                try:
                    for entry in sorted(path.iterdir()):
                        node["children"].append(build_tree(entry, node["id"], depth + 1))
                except PermissionError:
                    pass
            return node

        if not root_path.exists():
            return default_res

        tree = build_tree(root_path, "-1", 1)
        tree["name"] = workspace_name
        tree["id"] = "-1"
        tree["parentId"] = None
        tree["depth"] = 0
        return tree

    async def _generate_oss_tree(self, workspace_name: str, default_res: dict) -> dict:
        try:
            prefix = self.workspace_path.rstrip('/') + '/' if self.workspace_path else ''
            # oss storage special function: list all keys
            all_keys = self.storage.list_items(block_id=prefix)
            rel_keys = [key[len(prefix):] for key in all_keys if key.startswith(prefix)]

            root = default_res
            node_map = {"": root}

            for key in rel_keys:
                parts = [p for p in key.split('/') if p]
                cur_path = ""
                for depth, part in enumerate(parts):
                    parent_path = cur_path
                    cur_path = f"{cur_path}/{part}" if cur_path else part

                    if cur_path not in node_map:
                        node = {
                            "name": part,
                            "id": str(uuid.uuid4()),
                            "type": "dir" if depth < len(parts) - 1 else "file",
                            "parentId": node_map[parent_path]["id"],
                            "depth": depth + 1,
                            "expanded": False,
                            "children": []
                        }
                        node_map[parent_path]["children"].append(node)
                        node_map[cur_path] = node

            return root
        except Exception as e:
            logger.error(f"Failed to generate OSS tree: {e}")
            return default_res
