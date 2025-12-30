# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Freedom Space Service - Manages agent's freedom space (working directory) operations.

This service provides a fancy interface for managing agent's isolated file system workspace,
acting as a "freedom space" where agents can freely create, modify, and manage files.
Supports both local and remote (OSS) storage with automatic configuration.
"""
import abc
import hashlib
import os
from typing import Optional, Tuple, Any, Dict

from aworld.core.context.amni.retrieval.artifacts.file import DirArtifact
from aworld.output.artifact import ArtifactAttachment


class IFreedomSpaceService(abc.ABC):
    """Interface for freedom space (working directory) management operations."""
    
    @abc.abstractmethod
    async def init_freedom_space(self) -> DirArtifact:
        """
        Initialize freedom space (working directory).
        
        Creates a freedom space for the agent to store and manage files.
        Supports both local and remote (OSS) storage based on configuration.
        
        Returns:
            DirArtifact: Initialized freedom space artifact
        """
        pass
    
    @abc.abstractmethod
    async def load_freedom_space(self) -> DirArtifact:
        """
        Load freedom space and reload files.
        
        Returns:
            DirArtifact: Loaded freedom space artifact
        """
        pass
    
    @abc.abstractmethod
    async def refresh_freedom_space(self) -> None:
        """
        Refresh freedom space and sync to workspace.
        
        Reloads files from the freedom space and updates workspace.
        """
        pass
    
    @abc.abstractmethod
    def get_freedom_space_path(self) -> str:
        """
        Get freedom space base path.
        
        Returns:
            str: Base path of the freedom space
        """
        pass
    
    @abc.abstractmethod
    def get_env_mounted_path(self) -> str:
        """
        Get environment mounted path for freedom space.
        
        This is the path where the freedom space is mounted inside the environment.
        The agent accesses files through this mounted path.
        
        Returns:
            str: Environment mounted path
        """
        pass
    
    @abc.abstractmethod
    def get_abs_file_path(self, filename: str) -> str:
        """
        Get absolute file path in the environment.
        
        Args:
            filename: Name of the file
            
        Returns:
            str: Absolute file path in the environment
        """
        pass
    
    @abc.abstractmethod
    async def add_file(self, filename: Optional[str], content: Optional[Any], 
                      mime_type: Optional[str] = "text", namespace: str = "default",
                      origin_type: str = None, origin_path: str = None) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Add a file to freedom space.
        
        Args:
            filename: Name of the file
            content: File content
            mime_type: MIME type of the file
            namespace: Namespace for storage
            origin_type: Origin type of the file
            origin_path: Original path of the file
            
        Returns:
            Tuple of (success, file_path, content)
        """
        pass


class FreedomSpaceService(IFreedomSpaceService):
    """
    Freedom Space Service implementation.
    
    Manages agent's freedom space for file operations.
    Supports both local and remote (OSS) storage with automatic configuration.
    """
    
    def __init__(self, context):
        """
        Initialize FreedomSpaceService with ApplicationContext.
        
        Args:
            context: ApplicationContext instance that provides access to config and workspace
        """
        self._context = context
    
    async def init_freedom_space(self) -> DirArtifact:
        """Initialize freedom space (working directory)."""
        if self._context._working_dir:
            return self._context._working_dir

        # Generate stable artifact_id based on session_id to ensure uniqueness
        base_path = self._build_freedom_space_base_path()
        artifact_id = self._generate_working_dir_artifact_id(base_path)

        # Initialize freedom space
        if self._context._config.env_config.env_type == 'remote' and self._context._config.env_config.enabled_file_share:
            # Get OSS configuration with priority: config > environment variables
            oss_config = self._get_oss_config()
            self._context._working_dir = DirArtifact.with_oss_repository(
                access_key_id=oss_config['access_key_id'],
                access_key_secret=oss_config['access_key_secret'],
                endpoint=oss_config['endpoint'],
                bucket_name=oss_config['bucket_name'],
                base_path=base_path,
                mount_path=self._context.get_config().env_config.env_mount_path,
                artifact_id=artifact_id
            )
        else:
            # default local - use the same path building logic for consistency
            self._context._working_dir = DirArtifact.with_local_repository(
                base_path=base_path,
                artifact_id=artifact_id
            )

        return self._context._working_dir
    
    async def load_freedom_space(self) -> DirArtifact:
        """Load freedom space and reload files."""
        await self.init_freedom_space()
        self._context._working_dir.reload_working_files()
        return self._context._working_dir
    
    async def refresh_freedom_space(self) -> None:
        """Refresh freedom space and sync to workspace."""
        await self.init_freedom_space()
        self._context._working_dir.reload_working_files()
        workspace = await self._context._ensure_workspace()
        # add_artifact will check if artifact exists and update it if needed, avoiding duplicate creation
        await workspace.add_artifact(self._context._working_dir, index=False)
    
    def get_freedom_space_path(self) -> str:
        """Get freedom space base path."""
        return self._context._working_dir.base_path
    
    def get_env_mounted_path(self) -> str:
        """Get environment mounted path for freedom space."""
        return self._context._config.env_config.env_mount_path
    
    def get_abs_file_path(self, filename: str) -> str:
        """Get absolute file path in the environment."""
        return self.get_env_mounted_path() + "/" + filename
    
    async def add_file(self, filename: Optional[str], content: Optional[Any], 
                      mime_type: Optional[str] = "text", namespace: str = "default",
                      origin_type: str = None, origin_path: str = None) -> Tuple[bool, Optional[str], Optional[str]]:
        """Add a file to freedom space."""
        from aworld.output.artifact import ArtifactAttachment
        
        # Save metadata
        file = ArtifactAttachment(
            filename=filename,
            mime_type=mime_type,
            content=content,
            origin_type=origin_type,
            origin_path=origin_path
        )
        dir_artifact: DirArtifact = await self.load_freedom_space()
        # Persist the new file to the directory
        success, file_path, content = await dir_artifact.add_file(file)
        if not success:
            return False, None, None
        # Refresh directory index
        await self._context.knowledge_service.add_knowledge(dir_artifact, namespace, index=False)
        return True, self.get_abs_file_path(filename), content
    
    def _build_freedom_space_base_path(self) -> str:
        """
        Build freedom space base path with priority order.
        
        Priority:
        1. Config base_path (from context_config.env_config.working_dir_base_path)
        2. Environment variable WORKING_DIR_BASE_PATH
        3. Environment variable WORKING_DIR_OSS_BASE_PATH
        4. Environment variable WORKSPACE_PATH
        5. Default: ./data/workspaces
        
        Path template priority:
        1. Config template (from context_config.env_config.working_dir_path_template)
        2. Environment variable template (WORKING_DIR_PATH_TEMPLATE)
        3. Default: "{base_path}/{session_id}/files"
        
        Returns:
            str: Built freedom space path
        """
        from aworld.logs.util import logger
        
        # Get base_path with priority order
        base_path = None
        if self._context._config and self._context._config.env_config and self._context._config.env_config.working_dir_base_path:
            base_path = self._context._config.env_config.working_dir_base_path
        elif os.environ.get('WORKING_DIR_BASE_PATH'):
            base_path = os.environ.get('WORKING_DIR_BASE_PATH')
        elif os.environ.get('WORKING_DIR_OSS_BASE_PATH'):
            base_path = os.environ.get('WORKING_DIR_OSS_BASE_PATH')
        elif os.environ.get('WORKSPACE_PATH'):
            base_path = os.environ.get('WORKSPACE_PATH')
        else:
            base_path = './data/workspaces'
            if not os.path.exists(base_path):
                os.makedirs(base_path)
        
        # Get template with priority order
        config_template = None
        if self._context._config and self._context._config.env_config and self._context._config.env_config.working_dir_path_template:
            config_template = self._context._config.env_config.working_dir_path_template
        
        env_template = os.environ.get('WORKING_DIR_PATH_TEMPLATE')
        template = config_template or env_template or "{base_path}/{session_id}/files"
        
        # Replace placeholders
        try:
            path = template.format(
                base_path=base_path,
                session_id=self._context.session_id,
                task_id=self._context.task_id
            )
            return path
        except KeyError as e:
            # If template contains unsupported placeholders, fall back to default
            logger.warning(f"Unsupported placeholder in working_dir_path_template: {e}, using default template")
            return f"{base_path}/{self._context.session_id}/files"
    
    def _generate_working_dir_artifact_id(self, base_path: str) -> str:
        """
        Generate a stable artifact_id for working directory based on session_id and base_path.
        
        This ensures that the same session always uses the same artifact_id,
        preventing duplicate creation of working_dir artifacts.
        
        Args:
            base_path: Base path of the working directory
            
        Returns:
            str: Stable artifact_id for the working directory
        """
        # Use session_id as primary identifier, fallback to base_path hash if session_id is not available
        identifier = self._context.session_id if self._context.session_id else base_path
        # Generate a deterministic hash-based artifact_id
        artifact_id_hash = hashlib.md5(identifier.encode('utf-8')).hexdigest()
        return f"working_dir_{artifact_id_hash}"
    
    def _get_oss_config(self) -> Dict[str, Optional[str]]:
        """
        Get OSS configuration with priority order: config.working_dir_oss_config > environment variables.
        
        Returns:
            Dict containing OSS configuration: access_key_id, access_key_secret, endpoint, bucket_name
        """
        env_config = self._context._config.env_config if self._context._config and self._context._config.env_config else None
        oss_config = env_config.working_dir_oss_config if env_config and env_config.working_dir_oss_config else None
        
        # Priority: config.working_dir_oss_config > WORKING_DIR_OSS_* > OSS_*
        access_key_id = (
            oss_config.access_key_id if oss_config and oss_config.access_key_id
            else os.environ.get('WORKING_DIR_OSS_ACCESS_KEY_ID') or os.environ.get('OSS_ACCESS_KEY_ID')
        )
        
        access_key_secret = (
            oss_config.access_key_secret if oss_config and oss_config.access_key_secret
            else os.environ.get('WORKING_DIR_OSS_ACCESS_KEY_SECRET') or os.environ.get('OSS_ACCESS_KEY_SECRET')
        )
        
        endpoint = (
            oss_config.endpoint if oss_config and oss_config.endpoint
            else os.environ.get('WORKING_DIR_OSS_ENDPOINT') or os.environ.get('OSS_ENDPOINT')
        )
        
        bucket_name = (
            oss_config.bucket_name if oss_config and oss_config.bucket_name
            else os.environ.get('WORKING_DIR_OSS_BUCKET_NAME') or os.environ.get('OSS_BUCKET_NAME')
        )
        
        return {
            'access_key_id': access_key_id,
            'access_key_secret': access_key_secret,
            'endpoint': endpoint,
            'bucket_name': bucket_name
        }

