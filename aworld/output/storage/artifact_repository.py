import json
import os
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional, List

from pydantic import BaseModel

from aworld.output import Artifact


class ArtifactRepository:
    def __init__(self):
        """
        Initialize the artifact repository
        """
        pass

    def load_index(self) -> Dict[str, Any]:
        """Load or create index file"""
        pass

    def save_index(self, index_data: Dict[str, Any]) -> None:
        """Save index to file"""

    def store_artifact(self,
                       artifact: Artifact
                       ) -> str:
        """
        Store artifact and return its version identifier

        Args:
            artifact_id: Unique identifier of the artifact
            data: Data to be stored
            metadata: Optional metadata

        Returns:
            Version identifier
        """
        pass

    def delete_artifact(self, artifact_id: str) -> None:
        """
        Delete artifact from repository
        """
        pass

    def retrieve_latest_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        pass

    def get_artifact_versions(self, artifact_id: str) -> List[Dict[str, Any]]:
        """
        Get information about all versions of an artifact

        Args:
            artifact_id: Artifact identifier

        Returns:
            List of version information
        """
        pass
    def super_path(self) -> str:
        pass

    def artifact_path(self, artifact_id):
        return self.super_path() + f"/artifact/{artifact_id}/index.json"

    def generate_tree_data(self, workspace_name: str) -> dict:
        """
        Abstract method: Generate a directory tree structure for the workspace.
        Args:
            workspace_name: Name of the workspace (for root node)
        Returns:
            Directory tree as dict
        """
        raise NotImplementedError()


class CommonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.name
        if isinstance(obj, BaseModel):
            return obj.model_dump_json()
        return json.JSONEncoder.default(self, obj)

class EnumDecoder(json.JSONDecoder):
    def decode(self, s, **kwargs):
        parsed_json = super().decode(s, **kwargs)
        for key, value in parsed_json.items():
            if isinstance(value, dict) and value.get("__enum__"):
                enum_type = globals()[value["__enum_type__"]]
                enum_value = enum_type[value["__enum_value__"]]
                parsed_json[key] = enum_value
        return parsed_json


class LocalArtifactRepository(ArtifactRepository):
    """Artifact storage layer: manages versioned artifacts through content-addressable storage"""

    def __init__(self, storage_path: str):
        """
        Initialize the artifact repository
        
        Args:
            storage_path: Directory path for storing data
        """
        super().__init__()
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.index_path = self.storage_path / "index.json"
        self.index = self.load_index()

    def load_index(self) -> Dict[str, Any]:
        """Load or create index file"""
        if self.index_path.exists():
            try:
                with open(self.index_path, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {"artifacts": [], "versions": []}
        else:
            index = {"artifacts": [], "versions": []}
            self._save_index(index)
            return index

    def save_index(self, workspace_data) -> None:
        self._save_index(workspace_data)

    def _save_index(self, index: Dict[str, Any]) -> None:
        """Save index to file"""
        with open(self.index_path, 'w') as f:
            json.dump(index, f, indent=2, ensure_ascii=False, cls=CommonEncoder)

    def store_artifact(self,
                       artifact: Artifact
                       ) -> str:
        """
        Store artifact and return its version identifier
        
        Args:
            artifact: Artifact to be stored
            
        Returns:
            Version identifier
        """
        # Create version record
        version = {
            "hash": artifact.artifact_id,
            "timestamp": time.time(),
            "metadata": artifact.metadata or {}
        }

        # Update index

        data = artifact.to_dict()
        # Store content
        content_path = Path(self.artifact_path(artifact.artifact_id))
        content_path.parent.mkdir(parents=True, exist_ok=True)
        with open(content_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, cls=CommonEncoder)

        if artifact.attachments:
            for attachment in artifact.attachments:
                attachment_path = content_path.parent / attachment.filename
                attachment_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(attachment_path, 'w') as f:
                    f.write(attachment.content)



        return "success"

    def retrieve_latest_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve artifact based on version ID

        Args:
            version_id: Version identifier

        Returns:
            Stored data, or None if it doesn't exist
        """
        artifact_path = self.artifact_path(artifact_id)

        if not Path(artifact_path).exists():
            return None

        with open(artifact_path, 'r') as f:
            return json.load(f)

    def get_artifact_versions(self, artifact_id: str) -> List[Dict[str, Any]]:
        """
        Get information about all versions of an artifact
        
        Args:
            artifact_id: Artifact identifier
            
        Returns:
            List of version information
        """
        if artifact_id not in self.index["artifacts"]:
            return []

        versions = []
        for version_id in self.index["artifacts"][artifact_id]:
            version_info = self.index["versions"][version_id].copy()
            version_info["id"] = version_id
            versions.append(version_info)

        return versions
    
    def delete_artifact(self, artifact_id: str) -> bool:
        """
        Delete the specified artifact and its attachments from storage
        Args:
            artifact_id: Artifact identifier
        Returns:
            Whether deletion was successful
        """
        content_path = Path(self.artifact_path(artifact_id))
        if not Path(content_path).exists():
            return False
            
        # Delete the artifact file
        os.remove(content_path)
        
        # Delete the artifact directory and all its contents
        artifact_dir = content_path.parent
        if artifact_dir.exists():
            for item in artifact_dir.glob('**/*'):
                if item.is_file():
                    os.remove(item)
            for item in reversed(list(artifact_dir.glob('**/*'))):
                if item.is_dir():
                    os.rmdir(item)
            os.rmdir(artifact_dir)
        
        return True

    def super_path(self):
        return str(self.storage_path)

    def generate_tree_data(self, workspace_name: str) -> dict:
        """
        Generate a directory tree structure based on the actual local workspace folder structure.
        Args:
            workspace_name: Name of the workspace (for root node)
        Returns:
            Directory tree as dict
        """
        import os
        def build_tree(path: str, parent_id: str, depth: int = 1) -> dict:
            import uuid
            node = {
                "name": os.path.basename(path) or workspace_name,
                "id": str(uuid.uuid4()),
                "type": "dir" if os.path.isdir(path) else "file",
                "parentId": parent_id,
                "depth": depth,
                "expanded": False,
                "children": []
            }
            if os.path.isdir(path):
                for entry in sorted(os.listdir(path)):
                    full_path = os.path.join(path, entry)
                    node["children"].append(build_tree(full_path, node["id"], depth + 1))
            return node
        root_path = str(self.storage_path)
        import os
        if not os.path.exists(root_path):
            return {
                "name": workspace_name,
                "id": "-1",
                "children": []
            }
        tree = build_tree(root_path, "-1", 1)
        tree["name"] = workspace_name
        tree["id"] = "-1"
        tree["parentId"] = None
        tree["depth"] = 0
        return tree


