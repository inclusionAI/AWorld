# coding: utf-8
"""
oss.py
Utility class for OSS (Object Storage Service) operations.
Provides simple methods for data operations: upload, read, delete, update.
"""
import os
import json
import tempfile
from typing import Optional, Dict, List, Any, Tuple, Union, BinaryIO, TextIO, IO, AnyStr

from aworld.logs.util import logger


class OSSClient:
    """
    A utility class for OSS (Object Storage Service) operations.
    Provides methods for data operations: upload, read, delete, update.
    """
    
    def __init__(self, 
                 access_key_id: Optional[str] = None, 
                 access_key_secret: Optional[str] = None, 
                 endpoint: Optional[str] = None, 
                 bucket_name: Optional[str] = None,
                 enable_export: Optional[bool] = None):
        """
        Initialize OSSClient with credentials.
        
        Args:
            access_key_id: OSS access key ID. If None, will try to get from environment variable OSS_ACCESS_KEY_ID
            access_key_secret: OSS access key secret. If None, will try to get from environment variable OSS_ACCESS_KEY_SECRET
            endpoint: OSS endpoint. If None, will try to get from environment variable OSS_ENDPOINT
            bucket_name: OSS bucket name. If None, will try to get from environment variable OSS_BUCKET_NAME
            enable_export: Whether to enable OSS export. If None, will try to get from environment variable EXPORT_REPLAY_TRACE_TO_OSS
        """
        self.access_key_id = access_key_id or os.getenv('OSS_ACCESS_KEY_ID')
        self.access_key_secret = access_key_secret or os.getenv('OSS_ACCESS_KEY_SECRET')
        self.endpoint = endpoint or os.getenv('OSS_ENDPOINT')
        self.bucket_name = bucket_name or os.getenv('OSS_BUCKET_NAME')
        self.enable_export = enable_export if enable_export is not None else os.getenv("EXPORT_REPLAY_TRACE_TO_OSS", "false").lower() == "true"
        self.bucket = None
        self._initialized = False
        
    def initialize(self) -> bool:
        """
        Initialize the OSS client with the provided or environment credentials.
        
        Returns:
            bool: True if initialization is successful, False otherwise
        """
        if self._initialized:
            return True
            
        if not self.enable_export:
            logger.info("OSS export is disabled. Set EXPORT_REPLAY_TRACE_TO_OSS=true to enable.")
            return False
            
        if not all([self.access_key_id, self.access_key_secret, self.endpoint, self.bucket_name]):
            logger.warn("Missing required OSS credentials. Please provide all required parameters or set environment variables.")
            return False
            
        try:
            import oss2
            auth = oss2.Auth(self.access_key_id, self.access_key_secret)
            self.bucket = oss2.Bucket(auth, self.endpoint, self.bucket_name)
            self._initialized = True
            return True
        except ImportError:
            logger.warn("Failed to import oss2 module. Please install it with 'pip install oss2'.")
            return False
        except Exception as e:
            logger.warn(f"Failed to initialize OSS client. Error: {str(e)}")
            return False

    # ---- Basic Data Operation Methods ----
    
    def upload_data(self, data: Union[IO[AnyStr], str, bytes, dict], oss_key: str) -> bool:
        """
        Upload data to OSS. Supports various types of data:
        - In-memory file objects (IO[AnyStr])
        - Strings (str)
        - Bytes (bytes)
        - Dictionaries (dict), will be automatically converted to JSON
        - File paths (str)
        
        Args:
            data: Data to upload, can be a file object or other supported types
            oss_key: The key (path) in OSS where the data will be stored
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return False
            
        try:
            # Handle file objects
            if hasattr(data, 'read'):
                content = data.read()
                if isinstance(content, str):
                    content = content.encode('utf-8')
                self.bucket.put_object(oss_key, content)
                logger.info(f"Successfully uploaded memory file to OSS: {oss_key}")
                return True
                
            # Handle dictionaries
            if isinstance(data, dict):
                content = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.bucket.put_object(oss_key, content)
                return True
                
            # Handle strings
            if isinstance(data, str):
                # Check if it's a file path
                if os.path.isfile(data):
                    self.bucket.put_object_from_file(oss_key, data)
                    logger.info(f"Successfully uploaded file {data} to OSS: {oss_key}")
                    return True
                # Otherwise treat as string content
                content = data.encode('utf-8')
                self.bucket.put_object(oss_key, content)
                return True
                
            # Handle bytes
            self.bucket.put_object(oss_key, data)
            logger.info(f"Successfully uploaded data to OSS: {oss_key}")
            return True
        except Exception as e:
            logger.warn(f"Failed to upload data to OSS: {str(e)}")
            return False
    
    def read_data(self, oss_key: str, as_json: bool = False) -> Union[bytes, dict, str, None]:
        """
        Read data from OSS.
        
        Args:
            oss_key: The key (path) in OSS of the data to read
            as_json: If True, parse the data as JSON and return a dict
            
        Returns:
            The data as bytes, dict (if as_json=True), or None if failed
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return None
            
        try:
            # Read data
            result = self.bucket.get_object(oss_key)
            data = result.read()
            
            # Convert to string or JSON if requested
            if as_json:
                return json.loads(data)
            
            return data
        except Exception as e:
            logger.warn(f"Failed to read data from OSS: {str(e)}")
            return None
    
    def read_text(self, oss_key: str) -> Optional[str]:
        """
        Read text data from OSS.
        
        Args:
            oss_key: The key (path) in OSS of the text to read
            
        Returns:
            str: The text data, or None if failed
        """
        data = self.read_data(oss_key)
        if data is not None:
            try:
                return data.decode('utf-8')
            except Exception as e:
                logger.warn(f"Failed to decode data as UTF-8: {str(e)}")
        return None
    
    def delete_data(self, oss_key: str) -> bool:
        """
        Delete data from OSS.
        
        Args:
            oss_key: The key (path) in OSS of the data to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return False
            
        try:
            self.bucket.delete_object(oss_key)
            logger.info(f"Successfully deleted data from OSS: {oss_key}")
            return True
        except Exception as e:
            logger.warn(f"Failed to delete data from OSS: {str(e)}")
            return False
    
    def update_data(self, oss_key: str, data: Union[IO[AnyStr], str, bytes, dict]) -> bool:
        """
        Update data in OSS (delete and upload).
        
        Args:
            oss_key: The key (path) in OSS of the data to update
            data: New data to upload, can be a file object or other supported types
            
        Returns:
            bool: True if successful, False otherwise
        """
        # For OSS, update is the same as upload (it overwrites)
        return self.upload_data(data, oss_key)
    
    def update_json(self, oss_key: str, update_dict: dict) -> bool:
        """
        Update JSON data in OSS by merging with existing data.
        
        Args:
            oss_key: The key (path) in OSS of the JSON data to update
            update_dict: Dictionary with fields to update
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return False
            
        try:
            # Read existing data
            existing_data = self.read_data(oss_key, as_json=True)
            if existing_data is None:
                existing_data = {}
                
            # Update data
            if isinstance(existing_data, dict):
                existing_data.update(update_dict)
            else:
                logger.warn(f"Existing data is not a dictionary: {oss_key}")
                return False
                
            # Upload updated data
            return self.upload_data(existing_data, oss_key)
        except Exception as e:
            logger.warn(f"Failed to update JSON data in OSS: {str(e)}")
            return False
    
    # ---- File Operation Methods ----
    
    def upload_file(self, local_file: str, oss_key: Optional[str] = None) -> bool:
        """
        Upload a local file to OSS.
        
        Args:
            local_file: Path to the local file
            oss_key: The key (path) in OSS where the file will be stored. 
                     If None, will use the basename of the local file
                     
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return False
            
        try:
            if not os.path.exists(local_file):
                logger.warn(f"Local file {local_file} does not exist")
                return False
                
            if oss_key is None:
                oss_key = f"uploads/{os.path.basename(local_file)}"
                
            self.bucket.put_object_from_file(oss_key, local_file)
            logger.info(f"Successfully uploaded {local_file} to OSS: {oss_key}")
            return True
        except Exception as e:
            logger.warn(f"Failed to upload {local_file} to OSS: {str(e)}")
            return False
    
    def download_file(self, oss_key: str, local_file: str) -> bool:
        """
        Download a file from OSS to local.
        
        Args:
            oss_key: The key (path) in OSS of the file to download
            local_file: Path where the downloaded file will be saved
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return False
            
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(os.path.abspath(local_file)), exist_ok=True)
            
            # Download the file
            self.bucket.get_object_to_file(oss_key, local_file)
            logger.info(f"Successfully downloaded {oss_key} to {local_file}")
            return True
        except Exception as e:
            logger.warn(f"Failed to download {oss_key} from OSS: {str(e)}")
            return False
    
    def list_objects(self, prefix: str = "", delimiter: str = "") -> List[Dict[str, Any]]:
        """
        List objects in the OSS bucket with the given prefix.
        
        Args:
            prefix: Prefix to filter objects
            delimiter: Delimiter for hierarchical listing
            
        Returns:
            List of objects with their properties
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return []
            
        try:
            result = []
            for obj in self.bucket.list_objects(prefix=prefix, delimiter=delimiter).object_list:
                result.append({
                    'key': obj.key,
                    'size': obj.size,
                    'last_modified': obj.last_modified
                })
            return result
        except Exception as e:
            logger.warn(f"Failed to list objects with prefix {prefix}: {str(e)}")
            return []
    
    # ---- Advanced Operation Methods ----
    
    def exists(self, oss_key: str) -> bool:
        """
        Check if an object exists in OSS.
        
        Args:
            oss_key: The key (path) in OSS to check
            
        Returns:
            bool: True if the object exists, False otherwise
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return False
            
        try:
            # Use head_object to check if the object exists
            self.bucket.head_object(oss_key)
            return True
        except:
            return False
    
    def copy_object(self, source_key: str, target_key: str) -> bool:
        """
        Copy an object within the same bucket.
        
        Args:
            source_key: The source object key
            target_key: The target object key
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return False
            
        try:
            self.bucket.copy_object(self.bucket_name, source_key, target_key)
            logger.info(f"Successfully copied {source_key} to {target_key}")
            return True
        except Exception as e:
            logger.warn(f"Failed to copy {source_key} to {target_key}: {str(e)}")
            return False
    
    def get_object_url(self, oss_key: str, expires: int = 3600) -> Optional[str]:
        """
        Generate a temporary URL for accessing an object.
        
        Args:
            oss_key: The key (path) in OSS of the object
            expires: URL expiration time in seconds
            
        Returns:
            str: The signed URL, or None if failed
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return None
            
        try:
            url = self.bucket.sign_url('GET', oss_key, expires)
            return url
        except Exception as e:
            logger.warn(f"Failed to generate URL for {oss_key}: {str(e)}")
            return None
    
    def upload_directory(self, local_dir: str, oss_prefix: str = "") -> Tuple[bool, List[str]]:
        """
        Upload an entire directory to OSS.
        
        Args:
            local_dir: Path to the local directory
            oss_prefix: Prefix to prepend to all uploaded files
            
        Returns:
            Tuple of (success, list of uploaded files)
        """
        if not self.initialize():
            logger.warn("OSS client not initialized or export is disabled")
            return False, []
            
        if not os.path.isdir(local_dir):
            logger.warn(f"Local directory {local_dir} does not exist or is not a directory")
            return False, []
            
        uploaded_files = []
        errors = []
        
        for root, _, files in os.walk(local_dir):
            for file in files:
                local_file = os.path.join(root, file)
                rel_path = os.path.relpath(local_file, local_dir)
                oss_key = os.path.join(oss_prefix, rel_path).replace("\\", "/")
                
                success = self.upload_file(local_file, oss_key)
                if success:
                    uploaded_files.append(oss_key)
                else:
                    errors.append(local_file)
        
        if errors:
            logger.warn(f"Failed to upload {len(errors)} files")
            return False, uploaded_files
        return True, uploaded_files


def get_oss_client(access_key_id: Optional[str] = None, 
                  access_key_secret: Optional[str] = None, 
                  endpoint: Optional[str] = None, 
                  bucket_name: Optional[str] = None,
                  enable_export: Optional[bool] = None) -> OSSClient:
    """
    Factory function to create and initialize an OSSClient.
    
    Args:
        access_key_id: OSS access key ID
        access_key_secret: OSS access key secret
        endpoint: OSS endpoint
        bucket_name: OSS bucket name
        enable_export: Whether to enable OSS export
        
    Returns:
        OSSClient: An initialized OSSClient instance
    """
    client = OSSClient(
        access_key_id=access_key_id, 
        access_key_secret=access_key_secret, 
        endpoint=endpoint, 
        bucket_name=bucket_name,
        enable_export=enable_export
    )
    client.initialize()
    return client


# ---- Test Cases ----
if __name__ == "__main__":
    """
    OSS tool class test cases
    Note: Before running the tests, you need to set the following environment variables,
    or provide the parameters directly in the test code:
    - OSS_ACCESS_KEY_ID
    - OSS_ACCESS_KEY_SECRET
    - OSS_ENDPOINT
    - OSS_BUCKET_NAME
    - EXPORT_REPLAY_TRACE_TO_OSS=true
    """
    import io
    import time
    
    # Test configuration
    TEST_PREFIX = f"test/oss_utils_123"  # Use timestamp to avoid conflicts
    
    # Initialize client
    # Method 1: Using environment variables
    # oss_client = get_oss_client(enable_export=True)
    
    # Method 2: Provide parameters directly
    oss_client = get_oss_client(
        access_key_id="",  # Replace with your actual access key ID
        access_key_secret="",  # Replace with your actual access key secret
        endpoint="",  # Replace with your actual OSS endpoint
        bucket_name="",  # Replace with your actual bucket name
        enable_export=True
    )

    # Test 1: Upload string data
    print("\nTest 1: Upload string data")
    text_key = f"{TEST_PREFIX}/text.txt"
    success = oss_client.upload_data("This is a test text", text_key)
    print(f"Upload string data: {'Success' if success else 'Failed'}")

    # Test 2: Upload dictionary data (automatically converted to JSON)
    print("\nTest 2: Upload dictionary data")
    json_key = f"{TEST_PREFIX}/data.json"
    data = {
        "name": "Test data",
        "values": [1, 2, 3],
        "nested": {
            "key": "value"
        }
    }
    success = oss_client.upload_data(data, json_key)
    print(f"Upload dictionary data: {'Success' if success else 'Failed'}")

    # Test 3: Upload in-memory binary file object
    print("\nTest 3: Upload in-memory binary file object")
    binary_key = f"{TEST_PREFIX}/binary.dat"
    binary_data = io.BytesIO(b"\x00\x01\x02\x03\x04")
    success = oss_client.upload_data(binary_data, binary_key)
    print(f"Upload binary file object: {'Success' if success else 'Failed'}")

    # Test 4: Upload in-memory text file object
    print("\nTest 4: Upload in-memory text file object")
    text_file_key = f"{TEST_PREFIX}/text_file.txt"
    text_file = io.StringIO("This is the content of an in-memory text file")
    success = oss_client.upload_data(text_file, text_file_key)
    print(f"Upload text file object: {'Success' if success else 'Failed'}")

    # Test 5: Create and upload temporary file
    print("\nTest 5: Create and upload temporary file")
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b"This is the content of a temporary file")
        tmp_path = tmp.name

    file_key = f"{TEST_PREFIX}/temp_file.txt"
    success = oss_client.upload_file(tmp_path, file_key)
    print(f"Upload temporary file: {'Success' if success else 'Failed'}")
    os.unlink(tmp_path)  # Delete temporary file

    # Test 6: Read text data
    print("\nTest 6: Read text data")
    content = oss_client.read_text(text_key)
    print(f"Read text data: {content}")

    # Test 7: Read JSON data
    print("\nTest 7: Read JSON data")
    json_content = oss_client.read_data(json_key, as_json=True)
    print(f"Read JSON data: {json_content}")

    # Test 8: Update JSON data (merge method)
    print("\nTest 8: Update JSON data")
    update_data = {"updated": True, "timestamp": time.time()}
    success = oss_client.update_json(json_key, update_data)
    print(f"Update JSON data: {'Success' if success else 'Failed'}")

    # View updated JSON data
    updated_json = oss_client.read_data(json_key, as_json=True)
    print(f"Updated JSON data: {updated_json}")

    # Test 9: Overwrite existing data
    print("\nTest 9: Overwrite existing data")
    success = oss_client.upload_data("This is the overwritten text", text_key)
    print(f"Overwrite existing data: {'Success' if success else 'Failed'}")

    # View overwritten data
    new_content = oss_client.read_text(text_key)
    print(f"Overwritten text data: {new_content}")

    # Test 10: List objects
    print("\nTest 10: List objects")
    objects = oss_client.list_objects(prefix=TEST_PREFIX)
    print(f"Found {len(objects)} objects:")
    for obj in objects:
        print(f"  - {obj['key']} (Size: {obj['size']} bytes, Modified: {obj['last_modified']})")

    # Test 11: Generate temporary URL
    print("\nTest 11: Generate temporary URL")
    url = oss_client.get_object_url(text_key, expires=300)  # 5 minutes expiration
    print(f"Temporary URL: {url}")

    # Test 12: Copy object
    print("\nTest 12: Copy object")
    copy_key = f"{TEST_PREFIX}/copy_of_text.txt"
    success = oss_client.copy_object(text_key, copy_key)
    print(f"Copy object: {'Success' if success else 'Failed'}")

    # Test 13: Check if object exists
    print("\nTest 13: Check if object exists")
    exists = oss_client.exists(text_key)
    print(f"Object {text_key} exists: {exists}")

    non_existent_key = f"{TEST_PREFIX}/non_existent.txt"
    exists = oss_client.exists(non_existent_key)
    print(f"Object {non_existent_key} exists: {exists}")

    # Test 14: Delete objects
    print("\nTest 14: Delete objects")
    for obj in objects:
        success = oss_client.delete_data(obj['key'])
        print(f"Delete object {obj['key']}: {'Success' if success else 'Failed'}")

    # Cleanup: Delete copied object (may not be included in the previous list)
    oss_client.delete_data(copy_key)
    
    print("\nTests completed!") 