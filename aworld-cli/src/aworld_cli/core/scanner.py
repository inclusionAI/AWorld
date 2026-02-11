# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import glob
import os
import re
from typing import Optional, Dict, List

from aworld.core.context.amni.retrieval.artifacts.file.dir_artifact import DirArtifact
from aworld.logs.util import logger
from aworld.output.artifact import ArtifactAttachment


class Scanner(abc.ABC):
    """
    Base class for scanning and loading resources (e.g., agent, swarm).
    """
    
    def __init__(self, context):
        self._context = context
        
        # Initialize DirArtifact for storage
        self._dir_artifact = self._create_dir_artifact()

    def _get_storage_type(self) -> str:
        """Get storage type, 'local' or 'oss'"""
        return 'local'

    def _get_oss_config(self) -> Optional[Dict[str, str]]:
        """
        Get OSS configuration.
        
        Returns:
            OSS configuration dictionary containing access_key_id, access_key_secret, endpoint, bucket_name.
            Returns None if storage type is not OSS or configuration is unavailable.
        """
        storage_type = self._get_storage_type()
        if storage_type != 'oss':
            return None
        
        oss_config = None
        config = self._context.get_config() if hasattr(self._context, 'get_config') else None
        if config and hasattr(config, 'env_config') and config.env_config:
            oss_config_obj = config.env_config.working_dir_oss_config
            if oss_config_obj:
                oss_config = {
                    'access_key_id': oss_config_obj.access_key_id,
                    'access_key_secret': oss_config_obj.access_key_secret,
                    'endpoint': oss_config_obj.endpoint,
                    'bucket_name': oss_config_obj.bucket_name
                }
        
        # If not in config, try to get from environment variables
        if not oss_config or not all(oss_config.values()):
            oss_config = {
                'access_key_id': os.environ.get('OSS_ACCESS_KEY_ID'),
                'access_key_secret': os.environ.get('OSS_ACCESS_KEY_SECRET'),
                'endpoint': os.environ.get('OSS_ENDPOINT'),
                'bucket_name': os.environ.get('OSS_BUCKET_NAME')
            }
        
        return oss_config if oss_config and all(oss_config.values()) else None

    def _create_dir_artifact(self) -> DirArtifact:
        """
        Create and configure DirArtifact based on storage type.

        Returns:
            DirArtifact instance configured for the current storage type
        """
        storage_type = self._get_storage_type()
        base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))

        if storage_type == 'oss':
            # Get OSS configuration
            config = self._context.get_config() if hasattr(self._context, 'get_config') else None
            access_key_id = None
            access_key_secret = None
            endpoint = None
            bucket_name = None

            if config and hasattr(config, 'env_config') and config.env_config:
                oss_config = config.env_config.working_dir_oss_config
                if oss_config:
                    access_key_id = oss_config.access_key_id
                    access_key_secret = oss_config.access_key_secret
                    endpoint = oss_config.endpoint
                    bucket_name = oss_config.bucket_name

            # Fallback to environment variables
            if not access_key_id:
                access_key_id = os.environ.get('OSS_ACCESS_KEY_ID')
            if not access_key_secret:
                access_key_secret = os.environ.get('OSS_ACCESS_KEY_SECRET')
            if not endpoint:
                endpoint = os.environ.get('OSS_ENDPOINT')
            if not bucket_name:
                bucket_name = os.environ.get('OSS_BUCKET_NAME')

            return DirArtifact.with_oss_repository(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
                endpoint=endpoint,
                bucket_name=bucket_name,
                base_path=base_path
            )
        else:
            # Local storage
            return DirArtifact.with_local_repository(base_path)

    def _matches_file(self, attachment: ArtifactAttachment, name: str, suffix: str, base_path: str = None) -> bool:
        """
        Check if an attachment matches the resource name and suffix.
        Default implementation uses filename matching.
        Subclasses can override this to implement custom matching logic (e.g., content-based matching).
        
        Args:
            attachment: The attachment to check
            name: Resource name
            suffix: File suffix
            base_path: Base path for file access (optional, for content-based matching)
        
        Returns:
            True if the file matches, False otherwise
        """
        # Default implementation: filename-based matching
        # Check if filename matches the pattern
        if attachment.filename == f"{name}{suffix}":
            return True
        # Check if filename matches versioned pattern (for backward compatibility)
        pattern = re.compile(rf"^{re.escape(name)}_v\d+{re.escape(suffix)}$")
        return bool(pattern.match(attachment.filename))

    def _scan_files_by_suffix(self, suffix: str) -> List[str]:
        """
        Scan files by suffix and extract resource names.
        
        Args:
            suffix: File suffix (e.g., ".md")
        
        Returns:
            Sorted list of resource names
        """
        self._dir_artifact.reload_working_files()
        resource_names = set()
        base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))

        if self._dir_artifact.attachments:
            # Pattern to match directories: {resource_name}/ or {resource_name}_v{N}/ (for backward compatibility)
            dir_pattern = re.compile(rf"^([^/]+)/")
            
            for attachment in self._dir_artifact.attachments:
                if attachment.filename.endswith(suffix):
                    resource_name = None
                    # Extract resource name from directory path
                    match = dir_pattern.match(attachment.path)
                    if match:
                        dir_name = match.group(1)
                        # Remove version suffix if present (e.g., "agent_v1" -> "agent")
                        version_match = re.match(r'^(.+?)_v\d+$', dir_name)
                        if version_match:
                            resource_name = version_match.group(1)
                        else:
                            resource_name = dir_name
                    
                    if resource_name:
                        # Extract base filename without suffix for matching
                        file_base_name = attachment.filename[:-len(suffix)] if attachment.filename.endswith(suffix) else attachment.filename
                        # Use _matches_file to check if file matches (allows content-based matching in subclasses)
                        if self._matches_file(attachment, file_base_name, suffix, base_path):
                            resource_names.add(resource_name)

        return sorted(list(resource_names))

    async def _load_content_by_suffix(self, name: str, suffix: str) -> Optional[str]:
        """Load resource content from storage by suffix."""
        try:
            # Build filename and relative path
            filename = f"{name}{suffix}"
            # Try to find file in directory structure
            # First try without version suffix
            relative_path = f"{name}/{filename}"
            
            # For .md files, use DirArtifact
            if suffix == ".md":
                self._dir_artifact.reload_working_files()
                attachment = self._dir_artifact.get_file(relative_path)
                if not attachment:
                    # Try versioned path for backward compatibility
                    # Look for latest version
                    if self._dir_artifact.attachments:
                        version_pattern = re.compile(rf"^{re.escape(name)}_v(\d+)/{re.escape(filename)}$")
                        versions = []
                        for att in self._dir_artifact.attachments:
                            match = version_pattern.match(att.path)
                            if match:
                                versions.append(int(match.group(1)))
                        if versions:
                            latest_version = max(versions)
                            relative_path = f"{name}_v{latest_version}/{filename}"
                            attachment = self._dir_artifact.get_file(relative_path)
                
                if not attachment:
                    return None
                
                content = attachment.content
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                return content
            else:
                # For other suffixes (e.g., .yaml), read directly from filesystem
                base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))
                path = os.path.join(base_path, relative_path)
                
                if not os.path.exists(path):
                    # Try versioned path for backward compatibility
                    if os.path.exists(os.path.join(base_path, name)):
                        # Look for latest version
                        version_dirs = glob.glob(os.path.join(base_path, f"{name}_v*"))
                        if version_dirs:
                            latest_version_dir = max(version_dirs, key=lambda x: int(re.search(r'_v(\d+)$', x).group(1)) if re.search(r'_v(\d+)$', x) else 0)
                            path = os.path.join(latest_version_dir, filename)
                        else:
                            path = os.path.join(base_path, name, filename)
                
                if not os.path.exists(path):
                    return None
                
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()

        except Exception as e:
            logger.error(f"Failed to load content using suffix {suffix}: {e}")
            return None

    async def _load_as_source_by_suffix(
        self, 
        name: str, 
        suffix: str
    ) -> Optional[str]:
        """Load resource as source content by suffix."""
        # For .md files, use DirArtifact
        if suffix == ".md":
            attachment = await self.resolve_resource_from_artifact(
                self._dir_artifact, name, suffix
            )
            
            if attachment:
                content = attachment.content
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                return content
            return None

        # For other types, use _load_content_by_suffix
        content = await self._load_content_by_suffix(name=name, suffix=suffix)
        return content

    async def load_as_source(self, name: str) -> Optional[str]:
        """Load resource as source content (default implementation, subclasses should override to specify suffix)."""
        # Default implementation, subclasses should override this method and call _load_as_source_by_suffix with specified suffix
        raise NotImplementedError("Subclasses must implement load_as_source")

    @staticmethod
    async def resolve_resource_from_artifact(
        dir_artifact: DirArtifact,
        name: str,
        suffix: str
    ) -> Optional[ArtifactAttachment]:
        """Find resource in DirArtifact."""
        dir_artifact.reload_working_files()
        
        # Get file from directory
        filename = f"{name}{suffix}"
        # First try without version suffix
        path = f"{name}/{filename}"
        attachment = dir_artifact.get_file(path)
        
        if attachment:
            return attachment
        
        # Try to find latest versioned file for backward compatibility
        version_dir_pattern = re.compile(rf"^{re.escape(name)}_v(\d+)/{re.escape(filename)}$")
        versions = []
        
        if dir_artifact.attachments:
            for att in dir_artifact.attachments:
                if att.filename == filename:
                    match = version_dir_pattern.match(att.path)
                    if match:
                        versions.append(int(match.group(1)))
        
        if versions:
            latest_version = max(versions)
            path = f"{name}_v{latest_version}/{filename}"
            return dir_artifact.get_file(path)
        
        return None
