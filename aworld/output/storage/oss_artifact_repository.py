import hashlib
import json
import time
import uuid
from typing import Dict, Any, Optional, List, Literal
from .artifact_repository import ArtifactRepository, CommonEncoder


class OSSArtifactRepository(ArtifactRepository):
    """Artifact storage implementation based on Alibaba Cloud OSS"""

    def __init__(self, 
                 access_key_id: str,
                 access_key_secret: str,
                 endpoint: str,
                 bucket_name: str,
                 prefix: str = "aworld/workspaces/"):
        """
        Initialize OSS artifact repository
        
        Args:
            access_key_id: OSS access key ID
            access_key_secret: OSS access key secret
            endpoint: OSS service endpoint
            bucket_name: OSS bucket name
            prefix: Storage prefix, defaults to "artifacts/"
        """
        import oss2

        super().__init__()
        self.auth = oss2.Auth(access_key_id, access_key_secret)
        self.bucket = oss2.Bucket(self.auth, endpoint, bucket_name)
        self.prefix = prefix
        self.index_key = f"{prefix}index.json"
        self.index = self._load_index()

    def _load_index(self) -> Dict[str, Any]:
        """Load or create index file from OSS"""
        import oss2

        try:
            # Try to get index file from OSS
            result = self.bucket.get_object(self.index_key)
            content = result.read().decode('utf-8')
            return json.loads(content)
        except oss2.exceptions.NoSuchKey:
            # Index file doesn't exist, create new one
            index = {"artifacts": [], "versions": []}
            self._save_index(index)
            return index
        except Exception as e:
            print(f"Failed to load index file: {e}")
            return {"artifacts": [], "versions": []}

    def _save_index(self, index: Dict[str, Any]) -> None:
        """Save index file to OSS"""
        try:
            content = json.dumps(index, indent=2, ensure_ascii=False, cls=CommonEncoder)
            self.bucket.put_object(self.index_key, content.encode('utf-8'))
        except Exception as e:
            print(f"Failed to save index file: {e}")
            raise

    def _compute_content_hash(self, data: Any) -> str:
        """
        Calculate the hash value of the content as a storage identifier
        
        Args:
            data: Data to be stored
            
        Returns:
            SHA-256 hash value of the content
        """
        content = json.dumps(data, sort_keys=True, cls=CommonEncoder).encode('utf-8')
        return hashlib.sha256(content).hexdigest()

    def store(self,
              artifact_id: str,
              type: Literal['artifact', 'workspace'],
              data: Dict[str, Any],
              metadata: Optional[Dict[str, Any]] = None
              ) -> str:
        """
        Store artifact and return its version identifier
        
        Args:
            artifact_id: Unique identifier of the artifact
            type: Storage type, 'artifact' or 'workspace'
            data: Data to be stored
            metadata: Optional metadata
            
        Returns:
            Version identifier
        """
        import oss2

        try:
            # Calculate content hash
            content_hash = self._compute_content_hash(data)

            # Create version record
            version = {
                "hash": content_hash,
                "timestamp": time.time(),
                "metadata": metadata or {}
            }

            # Store content to OSS
            content_key = f"{self.prefix}{type}_{content_hash}.json"
            
            # Check if content already exists
            try:
                self.bucket.head_object(content_key)
                print(f"Content already exists: {content_key}")
            except oss2.exceptions.NoSuchKey:
                # Content doesn't exist, upload new content
                content = json.dumps(data, indent=2, ensure_ascii=False, cls=CommonEncoder)
                self.bucket.put_object(content_key, content.encode('utf-8'))

            # Update index
            if type == 'artifact':
                # Check if artifact already exists
                artifact_exists = False
                for artifact in self.index["artifacts"]:
                    if artifact['artifact_id'] == artifact_id:
                        # Update existing artifact version
                        artifact['version'] = version
                        artifact_exists = True
                        break
                
                if not artifact_exists:
                    # Add new artifact
                    self.index["artifacts"].append({
                        'artifact_id': artifact_id,
                        'type': type,
                        'version': version
                    })
                    
            elif type == 'workspace':
                version['version_id'] = str(uuid.uuid4())
                self.index["versions"].append(version)

            # Save updated index
            self._save_index(self.index)
            
            return "success"
            
        except Exception as e:
            print(f"Storage failed: {e}")
            raise

    def retrieve(self, version_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve artifact based on version ID
        
        Args:
            version_id: Version identifier
            
        Returns:
            Stored data, or None if it doesn't exist
        """
        import oss2

        try:
            # Find corresponding version in version list
            for version in self.index["versions"]:
                if version.get('version_id') == version_id:
                    content_hash = version["hash"]
                    content_key = f"{self.prefix}workspace_{content_hash}.json"
                    
                    try:
                        result = self.bucket.get_object(content_key)
                        content = result.read().decode('utf-8')
                        return json.loads(content)
                    except oss2.exceptions.NoSuchKey:
                        print(f"Content file doesn't exist: {content_key}")
                        return None
            
            return None
            
        except Exception as e:
            print(f"Retrieval failed: {e}")
            return None

    def retrieve_latest_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve the latest version of artifact based on artifact ID
        
        Args:
            artifact_id: Artifact identifier
            
        Returns:
            Stored data, or None if it doesn't exist
        """
        import oss2

        try:
            # Find corresponding artifact in artifact list
            for artifact in self.index["artifacts"]:
                if artifact['artifact_id'] == artifact_id:
                    content_hash = artifact["version"]["hash"]
                    content_key = f"{self.prefix}artifact_{content_hash}.json"
                    
                    try:
                        result = self.bucket.get_object(content_key)
                        content = result.read().decode('utf-8')
                        return json.loads(content)
                    except oss2.exceptions.NoSuchKey:
                        print(f"Content file doesn't exist: {content_key}")
                        return None
            
            return None
            
        except Exception as e:
            print(f"Failed to retrieve latest artifact: {e}")
            return None

    def get_versions(self, artifact_id: str) -> List[Dict[str, Any]]:
        """
        Get information about all versions of an artifact
        
        Args:
            artifact_id: Artifact identifier
            
        Returns:
            List of version information
        """
        try:
            # Find corresponding artifact
            for artifact in self.index["artifacts"]:
                if artifact['artifact_id'] == artifact_id:
                    # Return version information (simplified handling, only return current version)
                    version_info = artifact["version"].copy()
                    version_info["artifact_id"] = artifact_id
                    return [version_info]
            
            return []
            
        except Exception as e:
            print(f"Failed to get version information: {e}")
            return []

    def delete_artifact(self, artifact_id: str) -> bool:
        """
        Delete the specified artifact and all its versions
        
        Args:
            artifact_id: Artifact identifier
            
        Returns:
            Whether deletion was successful
        """
        import oss2

        try:
            # Find and delete artifact
            for i, artifact in enumerate(self.index["artifacts"]):
                if artifact['artifact_id'] == artifact_id:
                    content_hash = artifact["version"]["hash"]
                    content_key = f"{self.prefix}artifact_{content_hash}.json"
                    
                    # Delete content file from OSS
                    try:
                        self.bucket.delete_object(content_key)
                    except oss2.exceptions.NoSuchKey:
                        pass  # File already doesn't exist
                    
                    # Remove from index
                    del self.index["artifacts"][i]
                    self._save_index(self.index)
                    return True
            
            return False
            
        except Exception as e:
            print(f"Failed to delete artifact: {e}")
            return False

    def list_artifacts(self) -> List[Dict[str, Any]]:
        """
        List all artifacts
        
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
