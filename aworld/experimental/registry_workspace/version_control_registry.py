# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import difflib
import os
import re
from typing import Optional, Dict, List

from aworld.core.context.amni.retrieval.artifacts.file.dir_artifact import DirArtifact
from aworld.logs.util import logger
from aworld.output.artifact import ArtifactAttachment


class VersionControlRegistry(abc.ABC):
    """
    Base class for version control registry, used to manage version iterations of resources (e.g., agent, swarm).
    Each version update generates a new version, similar to checkpoint management in model training.
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
        base_path = os.path.expanduser(os.environ.get('AGENT_REGISTRY_STORAGE_PATH', './data/agent_registry'))

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
        # Check if filename matches versioned pattern
        pattern = re.compile(rf"^{re.escape(name)}_v\d+{re.escape(suffix)}$")
        return bool(pattern.match(attachment.filename))

    def _scan_files_by_suffix(self, suffix: str) -> List[str]:
        """
        Scan files by suffix and extract resource names.
        Version management is now directory-based: scans version directories for files with specified suffix.
        
        Args:
            suffix: File suffix (e.g., ".md")
        
        Returns:
            Sorted list of resource names
        """
        self._dir_artifact.reload_working_files()
        resource_names = set()
        base_path = os.path.expanduser(os.environ.get('AGENT_REGISTRY_STORAGE_PATH', './data/agent_registry'))

        if self._dir_artifact.attachments:
            # Pattern to match version directories: {resource_name}_v{N}/{filename} or {resource_name}/{filename} (v0)
            # Extract resource name from directory path
            version_dir_pattern = re.compile(rf"^([^_]+)_v\d+/")
            # Pattern to match directories without version suffix (v0): {resource_name}/{filename}
            v0_dir_pattern = re.compile(rf"^([^/]+)/([^/]+)$")
            
            for attachment in self._dir_artifact.attachments:
                if attachment.filename.endswith(suffix):
                    resource_name = None
                    # Check if file is in a versioned directory: {resource_name}_v{N}/
                    match = version_dir_pattern.match(attachment.path)
                    if match:
                        resource_name = match.group(1)
                    else:
                        # Check if file is in a directory without version suffix (v0): {resource_name}/{filename}
                        match_v0 = v0_dir_pattern.match(attachment.path)
                        if match_v0:
                            resource_name = match_v0.group(1)
                    
                    if resource_name:
                        # Extract base filename without suffix for matching
                        file_base_name = attachment.filename[:-len(suffix)] if attachment.filename.endswith(suffix) else attachment.filename
                        # Use _matches_file to check if file matches (allows content-based matching in subclasses)
                        if self._matches_file(attachment, file_base_name, suffix, base_path):
                            resource_names.add(resource_name)

        return sorted(list(resource_names))

    def _list_versions_by_suffix(self, name: str, suffix: str) -> List[str]:
        """List all versions of a resource by suffix."""
        versions = []
        self._dir_artifact.reload_working_files()
        base_path = os.path.expanduser(os.environ.get('AGENT_REGISTRY_STORAGE_PATH', './data/agent_registry'))
        
        # Pattern to match version directories: {name}_v{N}/ or {name}/ (v0)
        version_dir_pattern = re.compile(rf"^{re.escape(name)}(?:_v(\d+))?/")
        
        # Track which version directories we've seen
        seen_versions = set()
        
        if self._dir_artifact.attachments:
            for attachment in self._dir_artifact.attachments:
                if attachment.filename.endswith(suffix):
                    version = None
                    # Check if this file is in a versioned directory: {name}_v{N}/ or {name}/ (v0)
                    match = version_dir_pattern.match(attachment.path)
                    if match:
                        version_num = match.group(1)
                        if version_num is None:
                            # No _v suffix, treat as v0
                            version = "v0"
                        else:
                            version = f"v{version_num}"
                    
                    # Check if this directory contains a matching file
                    if version and version not in seen_versions:
                        # Extract the base name from filename (remove suffix)
                        file_base_name = attachment.filename[:-len(suffix)] if attachment.filename.endswith(suffix) else attachment.filename
                        # Use _matches_file to check if file matches (allows content-based matching in subclasses)
                        # This checks both filename pattern and optionally content (for Python files with @agent decorator)
                        if self._matches_file(attachment, file_base_name, suffix, base_path):
                            versions.append(version)
                            seen_versions.add(version)

        # Sort versions by version number
        versions.sort(key=self._extract_version_number)
        return versions

    async def apply_patch(
        self, 
        patch_content: str, 
        name: str, 
        suffix: str, 
        mime_type: str = None
    ) -> bool:
        """Apply patch to create a new version directory."""
        try:
            import shutil
            from pathlib import Path
            from aworld.output.artifact import ArtifactAttachment

            base_path = os.path.expanduser(os.environ.get('AGENT_REGISTRY_STORAGE_PATH', './data/agent_registry'))

            # Generate new version number
            new_version = await self.generate_new_version(name=name)
            
            # Get source and target directory paths
            # v0: {name}/, v1+: {name}_v{N}/
            if new_version == "v0":
                target_dir = Path(base_path) / name
            else:
                version_num = self._extract_version_number(new_version)
                target_dir = Path(base_path) / f"{name}_v{version_num}"
            
            # If not v0, copy from latest version
            if new_version != "v0":
                # Get latest version (which should be the one before new_version)
                versions = await self.list_versions(name)
                if versions and len(versions) > 0:
                    # Get the latest version (last in sorted list)
                    latest_version = versions[-1]
                    if latest_version == "v0":
                        source_dir = Path(base_path) / name
                    else:
                        latest_version_num = self._extract_version_number(latest_version)
                        source_dir = Path(base_path) / f"{name}_v{latest_version_num}"
                    
                    # Copy directory
                    if source_dir.exists():
                        if target_dir.exists():
                            shutil.rmtree(target_dir)
                        shutil.copytree(source_dir, target_dir)
                        logger.info(f"Copied directory from {source_dir.name} to {target_dir.name}")
                    else:
                        # Source directory doesn't exist, create new directory
                        target_dir.mkdir(parents=True, exist_ok=True)
                else:
                    # No versions exist, create new directory
                    target_dir.mkdir(parents=True, exist_ok=True)
            else:
                # v0: create new directory (without version suffix)
                target_dir.mkdir(parents=True, exist_ok=True)

            # Apply patch: update the file with specified suffix
            filename = f"{name}{suffix}"
            file_path = target_dir / filename
            
            # Write the patch content (or new content) to the file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(patch_content)
            
            logger.info(f"Applied patch to {file_path} (version: {new_version})")
            
            # Also add to DirArtifact for consistency
            if new_version == "v0":
                relative_path = f"{name}/{filename}"
            else:
                version_num = self._extract_version_number(new_version)
                relative_path = f"{name}_v{version_num}/{filename}"
            if mime_type is None:
                if suffix == ".md":
                    mime_type = 'text/markdown'
                elif suffix == ".yaml" or suffix == ".yml":
                    mime_type = 'text/yaml'
                else:
                    mime_type = 'text/plain'
            
            attachment = ArtifactAttachment(
                filename=filename,
                content=patch_content,
                mime_type=mime_type,
                path=relative_path
            )
            
            # Reload to include the new file
            self._dir_artifact.reload_working_files()
            
            return True

        except Exception as e:
            logger.error(f"Failed to apply patch: {e}")
            return False

    async def _save_file_by_suffix(
        self, 
        content: str, 
        name: str, 
        suffix: str, 
        mime_type: str = None
    ) -> bool:
        """Save file to storage by suffix."""
        return await self.apply_patch(
            patch_content=content,
            name=name,
            suffix=suffix,
            mime_type=mime_type
        )

    async def _load_content(self, name: str, version: str) -> Optional[str]:
        """Load resource content from storage (default uses .md suffix)."""
        return await self._load_content_by_suffix(name=name, suffix=".md", version=version)

    async def _load_content_by_suffix(self, name: str, suffix: str, version: str) -> Optional[str]:
        """Load resource content from storage by suffix."""
        try:
            # Build filename and relative path for directory-based structure
            filename = f"{name}{suffix}"
            if version == "v0":
                relative_path = f"{name}/{filename}"
            else:
                version_num = self._extract_version_number(version)
                relative_path = f"{name}_v{version_num}/{filename}"
            
            # For .md files, use DirArtifact
            if suffix == ".md":
                self._dir_artifact.reload_working_files()
                attachment = self._dir_artifact.get_file(relative_path)
                if not attachment:
                    return None
                
                content = attachment.content
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                return content
            else:
                # For other suffixes (e.g., .yaml), read directly from filesystem
                base_path = os.path.expanduser(os.environ.get('AGENT_REGISTRY_STORAGE_PATH', './data/agent_registry'))
                path = os.path.join(base_path, relative_path)
                
                if not os.path.exists(path):
                    return None
                
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()

        except Exception as e:
            logger.error(f"Failed to load content using suffix {suffix}: {e}")
            return None

    def _extract_version_number(self, version: str) -> int:
        """Extract version number from version string (e.g., "v0" -> 0, "v1" -> 1)."""
        match = re.match(r'v(\d+)', version)
        if match:
            return int(match.group(1))
        return 0


    @abc.abstractmethod
    async def list_versions(self, name: str) -> List[str]:
        """List all versions from storage (implemented by subclasses)."""
        pass

    async def get_latest_version(self, name: str) -> Optional[str]:
        """Get the latest version string for a resource."""
        versions = await self.list_versions(name)
        return versions[-1] if versions else None

    async def generate_new_version(self, name: str) -> str:
        """Generate a new version number by incrementing the latest existing version."""
        # Get latest version number and increment by 1
        versions = await self.list_versions(name)
        if versions:
            latest_version = versions[-1]
            latest_version_num = self._extract_version_number(latest_version)
            new_version_num = latest_version_num + 1
            new_version = f"v{new_version_num}"
        else:
            # No existing versions, start with v0
            new_version = "v0"

        return new_version

    async def compare_versions(self, name: str, format: str = "unified") -> Optional[str]:
        """Compare the latest version with the previous version of a resource's source content."""
        # Get all versions
        versions = await self.list_versions(name)
        
        if len(versions) < 2:
            logger.debug(f"Resource '{name}' has less than 2 versions, cannot compare")
            return None
        
        # Get latest and previous versions
        latest_version = versions[-1]
        previous_version = versions[-2]
        
        # Get source content for both versions
        latest_content = await self.load_as_source(name, latest_version)
        previous_content = await self.load_as_source(name, previous_version)
        
        # Convert bytes to string if needed
        if isinstance(latest_content, bytes):
            latest_content = latest_content.decode('utf-8')
        if isinstance(previous_content, bytes):
            previous_content = previous_content.decode('utf-8')
        
        if latest_content is None or previous_content is None:
            logger.warning(f"Failed to load content for comparison of resource '{name}'")
            return None
        
        # Split content into lines for diff
        latest_lines = latest_content.splitlines(keepends=True)
        previous_lines = previous_content.splitlines(keepends=True)
        
        # Generate diff based on format
        diff_lines = difflib.ndiff(
            previous_lines,
            latest_lines
        )
        
        # Join diff lines into a single string
        diff_result = ''.join(diff_lines)
        
        return diff_result

    @staticmethod
    async def resolve_resource_from_artifact(
        dir_artifact: DirArtifact,
        name: str,
        suffix: str,
        version: str = None
    ) -> Optional[ArtifactAttachment]:
        """Find resource in DirArtifact."""
        dir_artifact.reload_working_files()
        
        # 1. Find latest version if not specified
        if not version:
            versions = []
            # Pattern to match version directories: {name}_v{N}/ or {name}/ (v0)
            version_dir_pattern = re.compile(rf"^{re.escape(name)}(?:_v(\d+))?/")
            filename = f"{name}{suffix}"
            
            if dir_artifact.attachments:
                seen_versions = set()
                for attachment in dir_artifact.attachments:
                    if attachment.filename == filename:
                        # Check if this file is in a versioned directory: {name}_v{N}/ or {name}/ (v0)
                        match = version_dir_pattern.match(attachment.path)
                        if match:
                            version_num = match.group(1)
                            if version_num is None:
                                # No _v suffix, treat as v0
                                version_str = "v0"
                            else:
                                version_str = f"v{version_num}"
                            if version_str not in seen_versions:
                                versions.append(version_str)
                                seen_versions.add(version_str)
            
            if not versions:
                return None
            
            # Sort versions
            def extract_version_number(v: str) -> int:
                match = re.match(r'v(\d+)', v)
                return int(match.group(1)) if match else 0
            
            versions.sort(key=extract_version_number)
            version = versions[-1]
            
        # 2. Get file from version directory
        filename = f"{name}{suffix}"
        if version == "v0":
            path = f"{name}/{filename}"
        else:
            version_num = int(re.match(r'v(\d+)', version).group(1)) if re.match(r'v(\d+)', version) else 0
            path = f"{name}_v{version_num}/{filename}"
        return dir_artifact.get_file(path)

    async def _load_as_source_by_suffix(
        self, 
        name: str, 
        suffix: str, 
        version: str = None
    ) -> Optional[str]:
        """Load resource as source content by suffix."""
        # For .md files, use resolve_resource_from_artifact
        if suffix == ".md":
            attachment = await self.resolve_resource_from_artifact(
                self._dir_artifact, name, suffix, version
            )
            
            if attachment:
                content = attachment.content
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                return content
            return None

        # For other types, fall back to original logic (list_versions + _load_content_by_suffix)
        # Or directly use _load_content_by_suffix, as it also handles file reading
        
        if not version:
            versions = await self.list_versions(name=name)
            if not versions:
                return None
            version = versions[-1]

        # Load content
        content = await self._load_content_by_suffix(name=name, suffix=suffix, version=version)

        return content

    async def load_as_source(self, name: str, version: str = None) -> Optional[str]:
        """Load resource as source content (default implementation, subclasses should override to specify suffix)."""
        # Default implementation, subclasses should override this method and call _load_as_source_by_suffix with specified suffix
        raise NotImplementedError("Subclasses must implement load_as_source")
