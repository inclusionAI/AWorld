# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Freedom Space Service - Manages agent's freedom space (working directory) operations.

This service provides a fancy interface for managing agent's isolated file system workspace,
acting as a "freedom space" where agents can freely create, modify, and manage files.
Supports both local and remote (OSS) storage with automatic configuration.

Supports Hooks for three-layer file index:
1. File list index (filename, summary) for agent to find files at each layer.
2. File text index (full text per file).
3. File code index (Tree-Sitter def/ref + PageRank) for precise code positioning.
"""
import abc
import hashlib
import os
from typing import Any, Dict, List, Optional, Tuple

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

    @abc.abstractmethod
    async def add_files(
        self,
        files: List[Dict[str, Any]],
        namespace: str = "default",
        refresh_workspace: bool = True,
        build_index: Optional[bool] = None,
    ) -> List[Tuple[bool, Optional[str], Optional[str]]]:
        """
        Add multiple files to freedom space in batch; build index once at the end.

        Each item in files: dict with keys filename, content, mime_type (optional),
        origin_type (optional), origin_path (optional). Avoids repeated index build.

        Args:
            files: List of file dicts (filename, content, ...).
            namespace: Namespace for storage.
            refresh_workspace: Whether to add knowledge to workspace after batch.
            build_index: Whether to run three-layer file index once after batch;
                None to use env FREEDOM_SPACE_BUILD_INDEX.

        Returns:
            List of (success, file_path, content) per file, same order as files.
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
    
    async def load_freedom_space(self, build_index: Optional[bool] = None) -> DirArtifact:
        """Load freedom space and reload files. Optionally run three-layer file index hooks.
        When build_index is None, uses env FREEDOM_SPACE_BUILD_INDEX (1/true/yes=enabled, default enabled)."""
        await self.init_freedom_space()
        self._context._working_dir.reload_working_files()
        if build_index is None:
            from aworld.core.context.amni.indexing.env_config import is_build_index_enabled
            build_index = is_build_index_enabled()
        if build_index:
            await self._build_file_indexes_via_hooks(self._context._working_dir)
        return self._context._working_dir

    async def refresh_freedom_space(self, build_index: Optional[bool] = None) -> None:
        """Refresh freedom space and sync to workspace. Optionally run three-layer file index hooks.
        When build_index is None, uses env FREEDOM_SPACE_BUILD_INDEX (1/true/yes=enabled, default enabled)."""
        await self.init_freedom_space()
        self._context._working_dir.reload_working_files()
        if build_index is None:
            from aworld.core.context.amni.indexing.env_config import is_build_index_enabled
            build_index = is_build_index_enabled()
        if build_index:
            await self._build_file_indexes_via_hooks(self._context._working_dir)
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
                      origin_type: str = None, origin_path: str = None, refresh_workspace: bool = True) -> Tuple[bool, Optional[str], Optional[str]]:
        """Add a single file to freedom space. For multiple files use add_files() to build index once."""
        results = await self.add_files(
            [{
                "filename": filename,
                "content": content,
                "mime_type": mime_type,
                "origin_type": origin_type,
                "origin_path": origin_path,
            }],
            namespace=namespace,
            refresh_workspace=refresh_workspace,
            build_index=False,  # single file: skip index build; use add_files() for batch + index once
        )
        return results[0] if results else (False, None, None)

    async def add_files(
        self,
        files: List[Dict[str, Any]],
        namespace: str = "default",
        refresh_workspace: bool = True,
        build_index: Optional[bool] = None,
    ) -> List[Tuple[bool, Optional[str], Optional[str]]]:
        """
        Add multiple files in batch; build three-layer index once at the end.

        Example:
            results = await service.add_files([
                {"filename": "a.py", "content": "print(1)"},
                {"filename": "b.py", "content": "print(2)", "mime_type": "text"},
            ], build_index=True)
        """
        from aworld.logs.util import logger
        from aworld.output.artifact import ArtifactAttachment

        if not files:
            return []

        # Load freedom space once without building index
        await self.init_freedom_space()
        self._context._working_dir.reload_working_files()
        dir_artifact: DirArtifact = self._context._working_dir

        results: List[Tuple[bool, Optional[str], Optional[str]]] = []
        for item in files:
            filename = item.get("filename")
            content = item.get("content")
            mime_type = item.get("mime_type", "text")
            origin_type = item.get("origin_type")
            origin_path = item.get("origin_path")
            att = ArtifactAttachment(
                filename=filename,
                mime_type=mime_type,
                content=content,
                origin_type=origin_type,
                origin_path=origin_path,
            )
            success, file_path, out_content = await dir_artifact.add_file(att)
            if success:
                results.append((True, self.get_abs_file_path(filename), out_content))
            else:
                results.append((False, None, None))
                logger.warning(f"⚠️ add_files: failed to add file filename={filename or '?'}")

        if refresh_workspace:
            await self._context.knowledge_service.add_knowledge(dir_artifact, namespace, index=False)

        if build_index is None:
            from aworld.core.context.amni.indexing.env_config import is_build_index_enabled
            build_index = is_build_index_enabled()
        if build_index:
            await self._build_file_indexes_via_hooks(dir_artifact)

        logger.info(f"📁 add_files: batch added {len(files)} files, index built={build_index}")
        return results
    
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

    async def _build_file_indexes_via_hooks(self, dir_artifact: DirArtifact) -> Dict[str, Any]:
        """
        Run Hooks for three-layer file index and merge results into dir_artifact.metadata.

        Layer 1: file list index (filename, summary).
        Layer 2: file text index (full text per file).
        Layer 3: file code index (def/ref + PageRank).

        Args:
            dir_artifact: DirArtifact to index.

        Returns:
            Merged file_index dict stored in dir_artifact.metadata["file_index"].

        Example:
            await self._build_file_indexes_via_hooks(dir_artifact)
            index = dir_artifact.metadata.get("file_index", {})
        """
        from aworld.logs.util import logger
        from aworld.runners.hook.utils import run_hooks
        from aworld.runners.hook.hooks import HookPoint

        # Ensure default index hooks are registered (import side-effect)
        try:
            import aworld.core.context.amni.indexing.freedom_space_index_hooks  # noqa: F401
        except Exception as e:
            logger.debug(f"📁 Freedom space index hooks import: {e}")

        merged: Dict[str, Any] = {
            "file_list_index": [],
            "file_text_index": {},
            "file_code_index": None,
            "semantic_index": None,
        }
        context = self._context
        payload = {"dir_artifact": dir_artifact}
        hook_from = "FreedomSpaceService"

        for hook_point, key in [
            (HookPoint.FREEDOM_SPACE_FILE_LIST_INDEX, "file_list_index"),
            (HookPoint.FREEDOM_SPACE_FILE_TEXT_INDEX, "file_text_index"),
            (HookPoint.FREEDOM_SPACE_FILE_CODE_INDEX, "file_code_index"),
            (HookPoint.FREEDOM_SPACE_SEMANTIC_INDEX, "semantic_index"),
        ]:
            try:
                async for msg in run_hooks(
                    context=context,
                    hook_point=hook_point,
                    hook_from=hook_from,
                    payload=payload,
                ):
                    if msg and msg.payload and isinstance(msg.payload, dict):
                        val = msg.payload.get(key)
                        if val is not None:
                            if key == "file_text_index" and isinstance(val, dict):
                                merged[key].update(val)
                            elif key == "file_list_index" and isinstance(val, list):
                                merged[key] = val
                            elif key in ("file_code_index", "semantic_index"):
                                merged[key] = val
                            break
            except Exception as e:
                logger.warning(f"⚠️ Freedom space index hook {hook_point} failed: {e}")

        if dir_artifact.metadata is None:
            dir_artifact.metadata = {}
        dir_artifact.metadata["file_index"] = merged
        logger.info(
            f"📁 Freedom space file index built: list={len(merged['file_list_index'])} "
            f"text={len(merged['file_text_index'])} code={merged['file_code_index'] is not None} "
            f"semantic={merged['semantic_index'] is not None}"
        )
        return merged

