import os
import traceback
from typing import Union, Optional

from aworld.logs.util import logger

# Try to import optional dependencies
try:
    import oss2
    OSS2_AVAILABLE = True
except ImportError:
    OSS2_AVAILABLE = False
    oss2 = None

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None


class FileUtils:
    """Utility class for file operations."""
    
    # Text file extensions that can be read as text
    TEXT_EXTENSIONS = {
        '.txt', '.md', '.markdown', '.csv', '.json', '.jsonl',
        '.xml', '.html', '.htm', '.css', '.js', '.ts', '.tsx',
        '.py', '.java', '.cpp', '.c', '.h', '.hpp', '.go', '.rs',
        '.rb', '.php', '.swift', '.kt', '.scala', '.sh', '.bash',
        '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.log',
        '.sql', '.r', '.matlab', '.m', '.pl', '.pm', '.lua',
        '.vb', '.vbnet', '.cs', '.fs', '.fsx', '.dart', '.jl',
        '.clj', '.cljs', '.edn', '.ex', '.exs', '.erl', '.hrl',
        '.ml', '.mli', '.fsi', '.fs', '.fsx', '.v', '.sv', '.vhd',
        '.tex', '.bib', '.rtex', '.rts', '.rst', '.adoc', '.asciidoc',
        '.org', '.wiki', '.textile', '.creole', '.mediawiki'
    }
    
    @staticmethod
    def _is_text_file(filename: str) -> bool:
        """
        Check if a file is a text file based on its extension.
        
        Args:
            filename: The filename to check
            
        Returns:
            bool: True if the file is likely a text file, False otherwise
            
        Example:
            >>> FileUtils._is_text_file("test.txt")
            True
            >>> FileUtils._is_text_file("image.png")
            False
        """
        if not filename:
            return False
        
        # Get file extension
        _, ext = os.path.splitext(filename.lower())
        return ext in FileUtils.TEXT_EXTENSIONS
    
    @staticmethod
    async def _read_from_oss(origin_path: str, filename: str, is_text: bool) -> Optional[Union[str, bytes]]:
        """
        Read file content from OSS (Object Storage Service).
        
        Args:
            origin_path: The OSS key/path of the file
            filename: The filename (used for error messages)
            is_text: Whether the file should be read as text
            
        Returns:
            Optional[Union[str, bytes]]: File content as string or bytes, None if failed
        """
        if not OSS2_AVAILABLE:
            logger.error("❌ oss2 library is not installed. Cannot read from OSS.")
            return None
        
        try:
            # Parse OSS path - format: "endpoint|bucket|key" or just "key"
            # For simplicity, assume origin_path contains full key path
            # If it contains endpoint/bucket info, parse it
            parts = origin_path.split('|')
            if len(parts) == 3:
                endpoint, bucket_name, key = parts
                # Get credentials from environment
                access_key_id = os.getenv('DIR_ARTIFACT_OSS_ACCESS_KEY_ID') or os.getenv('OSS_ACCESS_KEY_ID')
                access_key_secret = os.getenv('DIR_ARTIFACT_OSS_ACCESS_KEY_SECRET') or os.getenv('OSS_ACCESS_KEY_SECRET')
                
                if not access_key_id or not access_key_secret:
                    logger.error("❌ OSS credentials not found in environment variables")
                    return None
                
                auth = oss2.Auth(access_key_id, access_key_secret)
                bucket = oss2.Bucket(auth, endpoint, bucket_name)
            else:
                # Assume origin_path is just the key, use default bucket from env
                access_key_id = os.getenv('DIR_ARTIFACT_OSS_ACCESS_KEY_ID') or os.getenv('OSS_ACCESS_KEY_ID')
                access_key_secret = os.getenv('DIR_ARTIFACT_OSS_ACCESS_KEY_SECRET') or os.getenv('OSS_ACCESS_KEY_SECRET')
                endpoint = os.getenv('DIR_ARTIFACT_OSS_ENDPOINT') or os.getenv('OSS_ENDPOINT')
                bucket_name = os.getenv('DIR_ARTIFACT_OSS_BUCKET_NAME') or os.getenv('OSS_BUCKET_NAME')
                
                if not all([access_key_id, access_key_secret, endpoint, bucket_name]):
                    logger.error("❌ OSS configuration not found in environment variables")
                    return None
                
                auth = oss2.Auth(access_key_id, access_key_secret)
                bucket = oss2.Bucket(auth, endpoint, bucket_name)
                key = origin_path
            
            # Read from OSS
            result = bucket.get_object(key)
            data = result.read()
            
            # Convert to text if needed
            if is_text:
                try:
                    return data.decode('utf-8')
                except UnicodeDecodeError:
                    # Try other common encodings
                    for encoding in ['gbk', 'gb2312', 'latin-1']:
                        try:
                            return data.decode(encoding)
                        except UnicodeDecodeError:
                            continue
                    logger.warning(f"⚠️ Failed to decode file {filename} as text, returning bytes")
                    return data
            
            return data
            
        except Exception as e:
            logger.error(f"❌ Error reading file {filename} from OSS: {e}")
            logger.debug(f"❌ Traceback: {traceback.format_exc()}")
            return None
    
    @staticmethod
    async def _read_from_local(origin_path: str, filename: str, is_text: bool) -> Optional[Union[str, bytes]]:
        """
        Read file content from local file system.
        
        Args:
            origin_path: The local file path
            filename: The filename (used for error messages)
            is_text: Whether the file should be read as text
            
        Returns:
            Optional[Union[str, bytes]]: File content as string or bytes, None if failed
        """
        try:
            if not os.path.exists(origin_path):
                logger.error(f"❌ File not found: {origin_path}")
                return None
            
            if not os.path.isfile(origin_path):
                logger.error(f"❌ Path is not a file: {origin_path}")
                return None
            
            # Read file based on type
            if is_text:
                try:
                    with open(origin_path, 'r', encoding='utf-8') as f:
                        return f.read()
                except UnicodeDecodeError:
                    # Try other common encodings
                    for encoding in ['gbk', 'gb2312', 'latin-1']:
                        try:
                            with open(origin_path, 'r', encoding=encoding) as f:
                                return f.read()
                        except UnicodeDecodeError:
                            continue
                    logger.warning(f"⚠️ Failed to decode file {filename} as text, reading as bytes")
                    with open(origin_path, 'rb') as f:
                        return f.read()
            else:
                with open(origin_path, 'rb') as f:
                    return f.read()
                    
        except Exception as e:
            logger.error(f"❌ Error reading file {filename} from local: {e}")
            logger.debug(f"❌ Traceback: {traceback.format_exc()}")
            return None
    
    @staticmethod
    async def _read_from_http(origin_path: str, filename: str, is_text: bool) -> Optional[Union[str, bytes]]:
        """
        Read file content from HTTP/HTTPS URL.
        
        Args:
            origin_path: The HTTP/HTTPS URL
            filename: The filename (used for error messages)
            is_text: Whether the file should be read as text
            
        Returns:
            Optional[Union[str, bytes]]: File content as string or bytes, None if failed
        """
        if not AIOHTTP_AVAILABLE:
            logger.error("❌ aiohttp library is not installed. Cannot read from HTTP.")
            return None
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(origin_path) as response:
                    if response.status != 200:
                        logger.error(f"❌ HTTP request failed with status {response.status} for {origin_path}")
                        return None
                    
                    data = await response.read()
                    
                    # Convert to text if needed
                    if is_text:
                        try:
                            return data.decode('utf-8')
                        except UnicodeDecodeError:
                            # Try other common encodings
                            for encoding in ['gbk', 'gb2312', 'latin-1']:
                                try:
                                    return data.decode(encoding)
                                except UnicodeDecodeError:
                                    continue
                            logger.warning(f"⚠️ Failed to decode file {filename} as text, returning bytes")
                            return data
                    
                    return data
                    
        except Exception as e:
            logger.error(f"❌ Error reading file {filename} from HTTP: {e}")
            logger.debug(f"❌ Traceback: {traceback.format_exc()}")
            return None
    
    @staticmethod
    async def read_origin_file_content(origin_type: str, origin_path: str, filename: str) -> Optional[Union[str, bytes]]:
        """
        Read file content from various origins (OSS, local, HTTP).
        
        This method automatically determines if a file is a text file based on its extension,
        and reads it accordingly (text files as string, binary files as bytes).
        
        Args:
            origin_type: Type of origin storage ("oss", "local", "http")
            origin_path: Path or URL to the file
            filename: Filename (used for type detection and error messages)
            
        Returns:
            Optional[Union[str, bytes]]: File content as string (for text files) or bytes (for binary files), None if failed
            
        Example:
            >>> # Read text file from local
            >>> content = await FileUtils.read_origin_file_content("local", "/path/to/file.txt", "file.txt")
            >>> # Read binary file from OSS
            >>> content = await FileUtils.read_origin_file_content("oss", "bucket/key/image.png", "image.png")
            >>> # Read CSV from HTTP
            >>> content = await FileUtils.read_origin_file_content("http", "https://example.com/data.csv", "data.csv")
        """
        if not origin_type or not origin_path:
            logger.error("❌ origin_type and origin_path are required")
            return None
        
        # Determine if file is text based on extension
        is_text = FileUtils._is_text_file(filename)
        
        # Route to appropriate reader based on origin_type
        if origin_type == "oss":
            return await FileUtils._read_from_oss(origin_path, filename, is_text)
        elif origin_type == "local":
            return await FileUtils._read_from_local(origin_path, filename, is_text)
        elif origin_type == "http" or origin_type == "https":
            return await FileUtils._read_from_http(origin_path, filename, is_text)
        else:
            logger.error(f"❌ Unsupported origin_type: {origin_type}")
            return None
