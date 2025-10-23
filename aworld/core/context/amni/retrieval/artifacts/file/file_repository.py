
import os
from abc import abstractmethod
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field

from aworld.logs.util import logger

# Try to import oss2 as optional dependency
try:
    import oss2
    OSS2_AVAILABLE = True
except ImportError:
    OSS2_AVAILABLE = False
    oss2 = None


class FileRepository(BaseModel):
    """Abstract base class for file repositories."""
    
    @abstractmethod
    def read_data(self, key: str) -> Optional[Any]:
        """Read data from the repository by key."""
        pass
    
    @abstractmethod
    def upload_data(self, key: str, data: Any, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Upload data to the repository with the given key."""
        pass
    
    @abstractmethod
    def delete_data(self, key: str) -> bool:
        """Delete data from the repository by key."""
        pass
    
    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if data exists in the repository by key."""
        pass
    
    @abstractmethod
    def list_files(self, prefix: str = "") -> List[Dict[str, Any]]:
        """List files in the repository with optional prefix filter."""
        pass


class LocalFileRepository(FileRepository):
    """Local file system repository implementation."""
    base_path: Optional[str] = Field(default="")

    def __init__(self, base_path: str):
        super().__init__()
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)
    
    def read_data(self, key: str) -> Optional[Any]:
        """Read data from local file system."""
        file_path = os.path.join(self.base_path, key)
        try:
            if os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    return f.read()
            return None
        except Exception as e:
            logger.error(f"❌ Error reading file {file_path}: {e}")
            return None
    
    def upload_data(self, key: str, data: Any, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Upload data to local file system with immediate disk sync.
        
        Args:
            key: File path where to save the data
            data: Data to be written (str or bytes)
            metadata: Optional metadata (not used in local implementation)
            
        Returns:
            bool: True if successful, False otherwise
            
        Example:
            >>> repo = LocalFileRepository("/tmp")
            >>> repo.upload_data("/tmp/test.txt", "Hello World")
            True
        """
        file_path = key
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            if isinstance(data, str):
                data = data.encode()
            with open(file_path, 'wb') as f:
                f.write(data)
            logger.info(f"✅ uploading file {file_path} success")
            return True
        except Exception as e:
            logger.error(f"❌ Error uploading file {file_path}: {e}")
            return False
    
    def delete_data(self, key: str) -> bool:
        """Delete data from local file system."""
        file_path = os.path.join(self.base_path, key)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Error deleting file {file_path}: {e}")
            return False
    
    def exists(self, key: str) -> bool:
        """Check if file exists in local file system."""
        file_path = os.path.join(self.base_path, key)
        return os.path.exists(file_path)
    
    def list_files(self, prefix: str = "") -> List[Dict[str, Any]]:
        """List files in local file system with optional prefix filter."""
        try:
            search_path = os.path.join(self.base_path, prefix) if prefix else self.base_path
            if not os.path.exists(search_path):
                return []
            
            files = []
            for item in os.listdir(search_path):
                item_path = os.path.join(search_path, item)
                if os.path.isfile(item_path):
                    # Get relative path from base_path
                    rel_path = os.path.relpath(item_path, self.base_path)
                    # Get file stats
                    stat = os.stat(item_path)
                    files.append({
                        'key': rel_path,
                        'filename': item,
                        'size': stat.st_size,
                        'modified_time': stat.st_mtime,
                        'is_file': True
                    })
            return files
        except Exception as e:
            logger.error(f"❌ Error listing files in local directory {search_path}: {e}")
            return []


class OssFileRepository(FileRepository):
    """OSS (Object Storage Service) repository implementation."""
    base_path: Optional[str] = Field(default="")
    bucket: Optional[Any] = Field(default=None)
    
    def __init__(self, 
                 access_key_id: Optional[str] = None,
                 access_key_secret: Optional[str] = None,
                 endpoint: Optional[str] = None,
                 bucket_name: Optional[str] = None,
                 enable_export: bool = True):
        """
        Initialize OSS file repository.
        
        Args:
            access_key_id: OSS access key ID (defaults to DIR_ARTIFACT_OSS_ACCESS_KEY_ID env var)
            access_key_secret: OSS access key secret (defaults to DIR_ARTIFACT_OSS_ACCESS_KEY_SECRET env var)
            endpoint: OSS endpoint (defaults to DIR_ARTIFACT_OSS_ENDPOINT env var)
            bucket_name: OSS bucket name (defaults to DIR_ARTIFACT_OSS_BUCKET_NAME env var)
            enable_export: Whether to enable export functionality
        """
        super().__init__()
        if not OSS2_AVAILABLE:
            logger.warning("⚠️ oss2 library is not installed. OssFileRepository will not be functional. "
                         "Install it with: pip install oss2")
        self._initialize_oss_client(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint=endpoint,
            bucket_name=bucket_name,
            enable_export=enable_export
        )
    
    def _initialize_oss_client(self, 
                              access_key_id: Optional[str] = None,
                              access_key_secret: Optional[str] = None,
                              endpoint: Optional[str] = None,
                              bucket_name: Optional[str] = None,
                              enable_export: bool = True):
        """Initialize OSS client using provided parameters or environment variables."""
        if not OSS2_AVAILABLE:
            self.bucket = None
            return
            
        try:
            # Use provided parameters or fall back to environment variables
            final_access_key_id = access_key_id or os.getenv('DIR_ARTIFACT_OSS_ACCESS_KEY_ID')
            final_access_key_secret = access_key_secret or os.getenv('DIR_ARTIFACT_OSS_ACCESS_KEY_SECRET')
            final_endpoint = endpoint or os.getenv('DIR_ARTIFACT_OSS_ENDPOINT')
            final_bucket_name = bucket_name or os.getenv('DIR_ARTIFACT_OSS_BUCKET_NAME')

            auth = oss2.Auth(final_access_key_id, final_access_key_secret)
            self.bucket = oss2.Bucket(auth, final_endpoint, final_bucket_name)
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize OSS client for OssFileRepository: {e}")
            self.bucket = None
    
    def read_data(self, key: str) -> Optional[Any]:
        """Read data from OSS."""
        if not self.bucket:
            logger.error("❌ OSS client not initialized")
            return None
        
        try:
            return self.bucket.get_object(key)
        except Exception as e:
            logger.error(f"❌ Error reading data from OSS with key {key}: {e}")
            return None
    
    def upload_data(self, key: str, data: Any, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Upload data to OSS."""
        if not self.bucket:
            logger.error("❌ OSS client not initialized")
            return False
        
        try:
            return self.bucket.put_object(key=key, data=data)
        except Exception as e:
            logger.error(f"❌ Error uploading data to OSS with key {key}: {e}")
            return False
    
    def delete_data(self, key: str) -> bool:
        """Delete data from OSS."""
        if not self.bucket:
            logger.error("❌ OSS client not initialized")
            return False
        
        try:
            return self.bucket.delete_object(key)
        except Exception as e:
            logger.error(f"❌ Error deleting data from OSS with key {key}: {e}")
            return False
    
    def exists(self, key: str) -> bool:
        """Check if data exists in OSS."""
        if not self.bucket:
            return False
        
        try:
            return self.bucket.get_object(key) is not None
        except Exception as e:
            logger.error(f"❌ Error checking existence in OSS with key {key}: {e}")
            return False
    
    def list_files(self, prefix: str = "") -> List[Dict[str, Any]]:
        """List files in OSS with optional prefix filter."""
        if not OSS2_AVAILABLE:
            logger.error("❌ oss2 library is not installed")
            return []
            
        if not self.bucket:
            logger.error("❌ OSS client not initialized")
            return []
        
        try:
            files = []
            # Use OSS list_objects_v2 to get objects with prefix
            for obj in oss2.ObjectIterator(self.bucket, prefix=prefix):
                # Skip directories (objects ending with '/')
                if not obj.key.endswith('/'):
                    files.append({
                        'key': obj.key,
                        'filename': os.path.basename(obj.key),
                        'size': obj.size,
                        'modified_time': obj.last_modified,
                        'is_file': True
                    })
            return files
        except Exception as e:
            logger.error(f"❌ Error listing files in OSS with prefix {prefix}: {e}")
            return []


