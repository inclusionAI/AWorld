# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import difflib
import os
import re
from typing import Optional, Dict, List

from aworld.core.context.amni.retrieval.artifacts.file.dir_artifact import DirArtifact
from aworld.output.artifact import ArtifactAttachment
from aworld.logs.util import logger


class VersionControlRegistry(abc.ABC):
    """
    Base class for version control registry, used to manage version iterations of resources (e.g., agent, swarm).
    Each version update generates a new version, similar to checkpoint management in model training.
    """
    
    def __init__(self, context):
        self._context = context
        # Cache for version lists per session
        self._version_cache: Dict[str, Dict[str, List[str]]] = {}
        # Cache for source content per session
        self._md_cache: Dict[str, Dict[str, Dict[str, str]]] = {}
        
        # Initialize DirArtifact for storage
        self._dir_artifact = self._create_dir_artifact()

    def _get_session_id(self, session_id: str = None) -> str:
        """Get session_id with fallback to context's session_id."""
        if session_id is None:
            session_id = self._context.session_id if hasattr(self._context, 'session_id') else "default"
        return session_id

    def _get_storage_type(self) -> str:
        """Get storage type, 'local' or 'oss'"""
        config = self._context.get_config() if hasattr(self._context, 'get_config') else None
        if config and hasattr(config, 'env_config') and config.env_config:
            registry_config = config.env_config.agent_registry_config
            if registry_config and hasattr(registry_config, 'storage_type') and registry_config.storage_type:
                storage_type = registry_config.storage_type.lower()
                if storage_type == 'oss':
                    return 'oss'
        return 'local'

    def _get_storage_base_path(self) -> str:
        """Get storage base path"""
        config = self._context.get_config() if hasattr(self._context, 'get_config') else None

        base_path = None
        if config and hasattr(config, 'env_config') and config.env_config:
            registry_config = config.env_config.agent_registry_config
            if registry_config and hasattr(registry_config, 'storage_base_path') and registry_config.storage_base_path:
                base_path = registry_config.storage_base_path

        if not base_path:
            base_path = os.environ.get('AGENT_REGISTRY_STORAGE_PATH', './data/agent_registry')

        return base_path

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
        base_path = self._get_storage_base_path()

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

        if self._dir_artifact.attachments:
            # Match root files (resource_name.md), exclude versioned files
            pattern_root = re.compile(rf"^(.+){re.escape(suffix)}$")
            for attachment in self._dir_artifact.attachments:
                match = pattern_root.match(attachment.filename)
                if match:
                    resource_name = match.group(1)
                    # Exclude versioned files (resource_name_vN.md), i.e., resource_name cannot end with _v followed by digits
                    if not re.match(r".+_v\d+$", resource_name):
                        resource_names.add(resource_name)

            # Match versioned files (resource_name_vN.md), extract resource_name
            pattern_versioned = re.compile(rf"^(.+)_v\d+{re.escape(suffix)}$")
            for attachment in self._dir_artifact.attachments:
                match = pattern_versioned.match(attachment.filename)
                if match:
                    resource_names.add(match.group(1))

        return sorted(list(resource_names))

    def _list_versions_by_suffix(self, name: str, suffix: str, session_id: str) -> List[str]:
        """
        List all versions of a resource by suffix.
        
        Scanning rules:
        - v0 version: in top-level directory {name}/{name}{suffix}
        - v1+ versions: in session directory {name}/{session_id}/{name}_vN{suffix}
        
        Args:
            name: Resource name
            suffix: File suffix (e.g., ".md")
            session_id: Session ID
        
        Returns:
            Sorted list of versions
        """
        versions = []
        self._dir_artifact.reload_working_files()

        # Check for attachments matching the resource name
        if self._dir_artifact.attachments:
            # Check for root v0 file (name.md) in top-level directory
            v0_filename = f"{name}{suffix}"
            v0_path_prefix = f"{name}/"
            for attachment in self._dir_artifact.attachments:
                if (attachment.filename == v0_filename and 
                    attachment.path.startswith(v0_path_prefix) and 
                    session_id not in attachment.path):
                    versions.append("v0")
                    break

            # Check for versioned files (name_vN.md) in session directory
            pattern = re.compile(rf"^{re.escape(name)}_v(\d+){re.escape(suffix)}$")
            for attachment in self._dir_artifact.attachments:
                match = pattern.match(attachment.filename)
                if match and session_id in attachment.path:
                    version = f"v{match.group(1)}"
                    if version not in versions:  # Avoid duplicates
                        versions.append(version)

        # Sort versions by version number
        versions.sort(key=self._extract_version_number)
        return versions

    async def _save_file_by_suffix(
        self, 
        content: str, 
        name: str, 
        suffix: str, 
        session_id: str = None,
        mime_type: str = None
    ) -> bool:
        """
        Save file to storage by suffix.
        
        First save (v0): save to top-level directory {name}/{name}{suffix}
        Subsequent saves (v1+): save to session directory {name}/{session_id}/{name}_vN{suffix}
        
        Args:
            content: File content
            name: Resource name
            suffix: File suffix (e.g., ".md")
            session_id: Optional session ID
            mime_type: MIME type, if None will be auto-detected based on suffix
        
        Returns:
            True if save successful, False otherwise
        """
        try:
            from aworld.output.artifact import ArtifactAttachment

            session_id = self._get_session_id(session_id)

            # Generate new version number
            new_version = await self.generate_new_version(name=name, session_id=session_id)

            # Create filename and path based on version
            if new_version == "v0":
                # First save: save to top-level directory
                filename = f"{name}{suffix}"
                file_path = f"{name}/{filename}"
            else:
                # Subsequent saves: save to session directory
                filename = f"{name}_{new_version}{suffix}"
                file_path = f"{name}/{session_id}/{filename}"

            # Auto-detect mime type if not provided
            if mime_type is None:
                if suffix == ".md":
                    mime_type = 'text/markdown'
                elif suffix == ".yaml" or suffix == ".yml":
                    mime_type = 'text/yaml'
                else:
                    mime_type = 'text/plain'

            attachment = ArtifactAttachment(
                filename=filename,
                content=content,
                mime_type=mime_type,
                path=file_path
            )

            success, saved_path, _ = await self._dir_artifact.add_file(attachment)

            if success:
                logger.info(f"Saved file: {saved_path} (version: {new_version})")
                # Clear cache to force reload
                if session_id in self._version_cache and name in self._version_cache[session_id]:
                    del self._version_cache[session_id][name]
                return True
            else:
                logger.error(f"Failed to save file: {name}")
                return False

        except Exception as e:
            logger.error(f"Failed to save file: {e}")
            return False

    async def _load_content(self, name: str, version: str, session_id: str) -> Optional[str]:
        """
        Load resource content from storage (default uses .md suffix).
        
        Args:
            name: Resource name
            version: Version number
            session_id: Session ID
        
        Returns:
            Resource content string, or None if not found
        """
        return await self._load_content_by_suffix(name=name, suffix=".md", version=version, session_id=session_id)

    async def _load_content_by_suffix(self, name: str, suffix: str, version: str, session_id: str) -> Optional[str]:
        """
        Load resource content from storage by suffix.
        
        Loading rules:
        - v0 version: from top-level directory {name}/{name}{suffix}
        - v1+ versions: from session directory {name}/{session_id}/{name}_vN{suffix}
        
        Args:
            name: Resource name
            suffix: File suffix (e.g., ".md" or ".yaml")
            version: Version number
            session_id: Session ID
        
        Returns:
            Resource content string, or None if not found
        """
        try:
            # Build filename and relative path
            if version == "v0":
                filename = f"{name}{suffix}"
                relative_path = f"{name}/{filename}"
            else:
                filename = f"{name}_{version}{suffix}"
                relative_path = f"{name}/{session_id}/{filename}"
            
            # For .md files, use DirArtifact
            if suffix == ".md":
                self._dir_artifact.reload_working_files()
                attachment = self._dir_artifact.get_file(filename)
                if not attachment:
                    return None
                
                content = attachment.content
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                return content
            else:
                # For other suffixes (e.g., .yaml), read directly from filesystem
                base_path = self._get_storage_base_path()
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

    async def _list_versions_with_cache(self, name: str, session_id: str = None) -> List[str]:
        """List all versions of a resource with caching."""
        session_id = self._get_session_id(session_id)

        # Check cache
        if session_id in self._version_cache:
            if name in self._version_cache[session_id]:
                versions = self._version_cache[session_id][name]
                return versions if versions else ["v0"]

        # Load from storage - call the abstract method
        versions = await self.list_versions(name, session_id)

        # Default to v0 if no versions found
        if not versions:
            versions = []

        # Update cache
        if session_id not in self._version_cache:
            self._version_cache[session_id] = {}
        self._version_cache[session_id][name] = versions

        return versions

    @abc.abstractmethod
    async def list_versions(self, name: str, session_id: str) -> List[str]:
        """
        List all versions from storage (implemented by subclasses).
        
        Args:
            name: Resource name
            session_id: Session ID
        
        Returns:
            List of versions
        """
        pass

    async def get_latest_version(self, name: str, session_id: str = None) -> Optional[str]:
        """Get the latest version string for a resource."""
        versions = await self._list_versions_with_cache(name, session_id)
        return versions[-1] if versions else None

    async def generate_new_version(self, name: str, session_id: str = None) -> str:
        """Generate a new version number by incrementing the latest existing version."""
        session_id = self._get_session_id(session_id)

        # Get latest version number and increment by 1
        versions = await self._list_versions_with_cache(name, session_id)
        if versions:
            latest_version = versions[-1]
            latest_version_num = self._extract_version_number(latest_version)
            new_version_num = latest_version_num + 1
            new_version = f"v{new_version_num}"
        else:
            # No existing versions, start with v0
            new_version = "v0"

        return new_version

    async def compare_versions(self, name: str, session_id: str = None, 
                               format: str = "unified") -> Optional[str]:
        """
        Compare the latest version with the previous version of a resource's source content.
        
        Args:
            name: The name of the resource to compare
            session_id: Optional session ID
            format: Diff format, either "unified" (default, like git diff) or "context"
        
        Returns:
            A string containing the diff, or None if there are less than 2 versions
        """
        session_id = self._get_session_id(session_id)
        
        # Get all versions
        versions = await self._list_versions_with_cache(name, session_id)
        
        if len(versions) < 2:
            logger.debug(f"Resource '{name}' has less than 2 versions, cannot compare")
            return None
        
        # Get latest and previous versions
        latest_version = versions[-1]
        previous_version = versions[-2]
        
        # Get source content for both versions
        latest_content = await self.load_as_source(name, session_id, latest_version)
        previous_content = await self.load_as_source(name, session_id, previous_version)
        
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
        session_id: str = None,
        version: str = None
    ) -> Optional[ArtifactAttachment]:
        """
        Find resource in DirArtifact.
        If version is None, automatically find the latest version.
        
        Scanning rules:
        - v0 version: in top-level directory {name}/{name}{suffix}
        - v1+ versions: in session directory {name}/{session_id}/{name}_vN{suffix}
        
        Args:
            dir_artifact: Directory artifact object
            name: Resource name
            suffix: File suffix
            session_id: Session ID
            version: Version number
            
        Returns:
            ArtifactAttachment object, or None if not found
        """
        dir_artifact.reload_working_files()
        
        # 1. Find latest version
        if not version:
            versions = []
            if dir_artifact.attachments:
                # Find v0 (in top-level directory)
                v0_filename = f"{name}{suffix}"
                v0_path_prefix = f"{name}/"
                for attachment in dir_artifact.attachments:
                    if (attachment.filename == v0_filename and 
                        attachment.path.startswith(v0_path_prefix) and 
                        session_id not in attachment.path):
                        versions.append("v0")
                        break
                
                # Find versioned files (in session directory)
                pattern = re.compile(rf"^{re.escape(name)}_v(\d+){re.escape(suffix)}$")
                for attachment in dir_artifact.attachments:
                    match = pattern.match(attachment.filename)
                    if match and session_id in attachment.path:
                        version = f"v{match.group(1)}"
                        if version not in versions:  # Avoid duplicates
                            versions.append(version)

            
            if not versions:
                return None
            
            # Sort versions
            def extract_version_number(v: str) -> int:
                match = re.match(r'v(\d+)', v)
                return int(match.group(1)) if match else 0
            
            versions.sort(key=extract_version_number)
            version = versions[-1]
            
        # 2. Get file
        filename = f"{name}{suffix}" if version == "v0" else f"{name}_{version}{suffix}"
        return dir_artifact.get_file(filename)

    async def _load_as_source_by_suffix(
        self, 
        name: str, 
        suffix: str, 
        session_id: str = None, 
        version: str = None
    ) -> Optional[str]:
        """
        Load resource as source content by suffix.
        
        Args:
            name: Resource name
            suffix: File suffix (e.g., ".md" or ".yaml")
            session_id: Optional session ID
            version: Optional version number, if None uses latest version
        
        Returns:
            Resource content string, or None if not found
        """
        session_id = self._get_session_id(session_id)

        # Check md cache first
        if session_id in self._md_cache:
            if name in self._md_cache[session_id]:
                if version:
                    if version in self._md_cache[session_id][name]:
                        return self._md_cache[session_id][name][version]
                else:
                    # If version not specified, we can still parse latest version from Artifact, or rely on cache?
                    # For simplicity, if cache has data, we can assume it's the latest?
                    # But for correctness, we should still go through resolve process, or list versions first
                    pass 

        # For .md files, use resolve_resource_from_artifact
        if suffix == ".md":
            attachment = await self.resolve_resource_from_artifact(
                self._dir_artifact, name, suffix, session_id, version
            )
            
            if attachment:
                content = attachment.content
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                
                # Try to parse version number to update cache
                resolved_version = version
                if not resolved_version:
                    # Infer version from filename
                    if attachment.filename == f"{name}{suffix}":
                        resolved_version = "v0"
                    else:
                        match = re.match(rf"^{re.escape(name)}_v(\d+){re.escape(suffix)}$", attachment.filename)
                        if match:
                            resolved_version = f"v{match.group(1)}"
                
                if resolved_version:
                    if session_id not in self._md_cache:
                        self._md_cache[session_id] = {}
                    if name not in self._md_cache[session_id]:
                        self._md_cache[session_id][name] = {}
                    self._md_cache[session_id][name][resolved_version] = content
                
                return content
            return None

        # For other types, fall back to original logic (list_versions + _load_content_by_suffix)
        # Or directly use _load_content_by_suffix, as it also handles file reading
        
        if not version:
            versions = await self.list_versions(name=name, session_id=session_id)
            if not versions:
                return None
            version = versions[-1]

        # Load content and cache it
        content = await self._load_content_by_suffix(name=name, suffix=suffix, version=version, session_id=session_id)
        
        if content is not None:
            # Cache the content
            if session_id not in self._md_cache:
                self._md_cache[session_id] = {}
            if name not in self._md_cache[session_id]:
                self._md_cache[session_id][name] = {}
            self._md_cache[session_id][name][version] = content

        return content

    async def load_as_source(self, name: str, session_id: str = None, version: str = None) -> Optional[str]:
        """
        Load resource as source content (default implementation, subclasses should override to specify suffix).
        
        Args:
            name: Resource name
            session_id: Optional session ID
            version: Optional version number, if None uses latest version
        
        Returns:
            Resource content string, or None if not found
        """
        # Default implementation, subclasses should override this method and call _load_as_source_by_suffix with specified suffix
        raise NotImplementedError("Subclasses must implement load_as_source")
