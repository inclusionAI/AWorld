import hashlib
import json
import time
import uuid
from typing import Dict, Any, Optional, List, Literal
from .artifact_repository import ArtifactRepository, CommonEncoder
from aworld.output.artifact import Artifact, ArtifactAttachment


class OSSArtifactRepository(ArtifactRepository):
    """
    Artifact storage implementation based on Alibaba Cloud OSS, similar to LocalArtifactRepository but using OSS as backend.
    """
    def __init__(self,
                 access_key_id: str,
                 access_key_secret: str,
                 endpoint: str,
                 bucket_name: str,
                 storage_path: str = "aworld/workspaces/"):
        """
        Initialize OSS artifact repository
        Args:
            access_key_id: OSS access key ID
            access_key_secret: OSS access key secret
            endpoint: OSS service endpoint
            bucket_name: OSS bucket name
            storage_path: Storage prefix, defaults to "aworld/workspaces/"
        """
        import oss2

        super().__init__()
        self.auth = oss2.Auth(access_key_id, access_key_secret)
        self.bucket = oss2.Bucket(self.auth, endpoint, bucket_name)
        self.prefix = storage_path.rstrip('/') + '/'
        self.index_key = f"{self.prefix}index.json"
        self.index = self.load_index()

    def load_index(self) -> Dict[str, Any]:
        """
        Load or create index file from OSS
        Returns:
            Index dictionary
        """
        import oss2

        try:
            result = self.bucket.get_object(self.index_key)
            content = result.read().decode('utf-8')
            return json.loads(content)
        except oss2.exceptions.NoSuchKey:
            index = {"artifacts": [], "versions": []}
            self._save_index(index)
            return index
        except Exception as e:
            print(f"Failed to load index file: {e}")
            return {"artifacts": [], "versions": []}

    def save_index(self, index: Dict[str, Any]) -> None:
        """
        Save the current index to OSS
        """
        self._save_index(index)

    def _save_index(self, index: Dict[str, Any]) -> None:
        """
        Save index file to OSS
        Args:
            index: Index dictionary
        """
        try:
            content = json.dumps(index, indent=2, ensure_ascii=False, cls=CommonEncoder)
            self.bucket.put_object(self.index_key, content.encode('utf-8'))
        except Exception as e:
            print(f"Failed to save index file: {e}")
            raise

    def artifact_path(self, artifact_id: str) -> str:
        """
        Get the OSS path for a given artifact
        Args:
            artifact_id: Artifact identifier
        Returns:
            OSS path string
        """
        return f"{self.prefix}artifact/{artifact_id}/index.json"

    def attachment_path(self, artifact_id: str, filename: str) -> str:
        """
        Get the OSS path for an artifact attachment
        Args:
            artifact_id: Artifact identifier
            filename: Attachment filename
        Returns:
            OSS path string
        """
        return f"{self.prefix}artifact/{artifact_id}/attachments/{filename}"

    def store_artifact(self, artifact: Artifact) -> str:
        """
        Store artifact and its attachments to OSS
        Args:
            artifact: Artifact instance to be stored
        Returns:
            Version identifier (always 'success' for now)
        """
        import oss2

        try:
            # Prepare version record
            version = {
                "hash": artifact.artifact_id,
                "timestamp": time.time(),
                "metadata": artifact.metadata or {}
            }
            # Store artifact content
            data = artifact.to_dict()
            content_key = self.artifact_path(artifact.artifact_id)
            content = json.dumps(data, indent=2, ensure_ascii=False, cls=CommonEncoder)
            self.bucket.put_object(content_key, content.encode('utf-8'))
            # Store attachments if any
            if artifact.attachments:
                for attachment in artifact.attachments:
                    if isinstance(attachment, ArtifactAttachment):
                        attachment_key = self.attachment_path(artifact.artifact_id, attachment.filename)
                        self.bucket.put_object(attachment_key, attachment.content.encode('utf-8'))
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
            self._save_index(self.index)
            return "success"
        except Exception as e:
            print(f"Storage failed: {e}")
            raise

    def retrieve_latest_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve the latest version of artifact based on artifact ID
        Args:
            artifact_id: Artifact identifier
        Returns:
            Stored data as dict, or None if it doesn't exist
        """
        import oss2

        try:
            content_key = self.artifact_path(artifact_id)
            try:
                result = self.bucket.get_object(content_key)
                content = result.read().decode('utf-8')
                return json.loads(content)
            except oss2.exceptions.NoSuchKey:
                print(f"Content file doesn't exist: {content_key}")
                return None
        except Exception as e:
            print(f"Failed to retrieve latest artifact: {e}")
            return None

    def get_artifact_versions(self, artifact_id: str) -> List[Dict[str, Any]]:
        """
        Get information about all versions of an artifact (currently only latest version is tracked)
        Args:
            artifact_id: Artifact identifier
        Returns:
            List of version information
        """
        try:
            for artifact in self.index["artifacts"]:
                if artifact['artifact_id'] == artifact_id:
                    version_info = artifact["version"].copy()
                    version_info["artifact_id"] = artifact_id
                    return [version_info]
            return []
        except Exception as e:
            print(f"Failed to get version information: {e}")
            return []

    def delete_artifact(self, artifact_id: str) -> bool:
        """
        Delete the specified artifact and its attachments from OSS
        Args:
            artifact_id: Artifact identifier
        Returns:
            Whether deletion was successful
        """
        import oss2

        try:
            # Delete artifact content
            content_key = self.artifact_path(artifact_id)
            try:
                self.bucket.delete_object(content_key)
            except oss2.exceptions.NoSuchKey:
                pass
            # Delete attachments (list objects under attachments/)
            attachment_prefix = f"{self.prefix}artifact/{artifact_id}/attachments/"
            for obj in oss2.ObjectIterator(self.bucket, prefix=attachment_prefix):
                self.bucket.delete_object(obj.key)
            # Remove from index
            for i, artifact in enumerate(self.index["artifacts"]):
                if artifact['artifact_id'] == artifact_id:
                    del self.index["artifacts"][i]
                    self._save_index(self.index)
                    return True
            return False
        except Exception as e:
            print(f"Failed to delete artifact: {e}")
            return False

    def list_artifacts(self) -> List[Dict[str, Any]]:
        """
        List all artifacts in the repository
        Returns:
            List of artifact information
        """
        try:
            return [
                {
                    "artifact_id": artifact["artifact_id"],
                    "type": artifact["type"],
                    "timestamp": artifact["version"]["timestamp"],
                    "metadata": artifact["version"]["metadata"]
                }
                for artifact in self.index["artifacts"]
            ]
        except Exception as e:
            print(f"Failed to list artifacts: {e}")
            return []
    

    def generate_tree_data(self, workspace_name: str) -> dict:
        """
        Generate a directory tree structure based on the OSS workspace folder structure.
        Args:
            workspace_name: Name of the workspace (for root node)
        Returns:
            Directory tree as dict
        """
        import oss2

        all_keys = [obj.key for obj in oss2.ObjectIterator(self.bucket, prefix=self.prefix)]
        # remove root prefix
        rel_keys = [key[len(self.prefix):] for key in all_keys if key != self.index_key]
        # build tree
        root = {
            "name": workspace_name,
            "id": "-1",
            "type": "dir",
            "parentId": None,
            "depth": 0,
            "expanded": False,
            "children": []
        }
        node_map = {"": root}  # 路径到节点的映射
        for key in rel_keys:
            parts = [p for p in key.split('/') if p]
            cur_path = ""
            parent_path = ""
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
