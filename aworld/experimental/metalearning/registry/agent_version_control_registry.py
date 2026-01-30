# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import re
import traceback
from pathlib import Path
from threading import RLock
from typing import Optional, Dict, List

from aworld.agents.llm_agent import Agent
from aworld.core.context.amni import DirArtifact
from aworld.experimental.metalearning.registry.version_control_registry import VersionControlRegistry
from aworld.logs.util import logger
from aworld.output.artifact import ArtifactAttachment
from aworld_cli.core.markdown_agent_loader import parse_markdown_agent


class AgentDslVersionControlRegistry(VersionControlRegistry):
    def __init__(self, context):
        VersionControlRegistry.__init__(self, context)
        self._lock = RLock()

    async def get_agent_from_base_path(
        self,
        base_path: str,
        agent_name: str,
        version: str = None,
        storage_type: str = "local",
        oss_config: Optional[Dict[str, str]] = None
    ) -> Optional[Agent]:
        """
        Static method to load agent from base_path.
        
        Args:
            base_path: Base path of the agent registry
            agent_name: Agent name
            version: Agent version, if None uses the latest version
            storage_type: Storage type, "local" or "oss", defaults to "local"
            oss_config: OSS configuration dictionary containing access_key_id, access_key_secret, endpoint, bucket_name
        
        Returns:
            Agent instance, or None if not found
        """
        try:
            # Create DirArtifact
            if storage_type == 'oss' and oss_config:
                dir_artifact = DirArtifact.with_oss_repository(
                    access_key_id=oss_config.get('access_key_id'),
                    access_key_secret=oss_config.get('access_key_secret'),
                    endpoint=oss_config.get('endpoint'),
                    bucket_name=oss_config.get('bucket_name'),
                    base_path=base_path
                )
            else:
                dir_artifact = DirArtifact.with_local_repository(base_path)
            
            # Use VersionControlRegistry static method to load resource
            attachment = await VersionControlRegistry.resolve_resource_from_artifact(
                dir_artifact=dir_artifact,
                name=agent_name,
                suffix=".md",
                version=version
            )
            
            if not attachment:
                return None
            
            # Parse markdown agent
            file_path = Path(dir_artifact.base_path + '/' + attachment.path)
            local_agent = parse_markdown_agent(file_path)
            
            if local_agent:
                swarm = await local_agent.get_swarm()
                if swarm and swarm.agents:
                    # Return the first agent in the swarm
                    agent_id, agent = next(iter(swarm.agents.items()))
                    return agent
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to load agent from base_path {base_path}: {e}")
            return None

    async def list_versions(self, name: str) -> List[str]:
        """List all versions of a resource by scanning .md files."""
        return self._list_versions_by_suffix(name=name, suffix=".md")

    async def list_as_source(self) -> List[str]:
        """List all available resources by scanning .md files."""
        return self._scan_files_by_suffix(".md")

    async def list_desc(self) -> List[tuple]:
        """
        List all available resources with their descriptions.
        
        Args:        
        Returns:
            List of tuples (name, description) for each resource
        """
        resources = self._scan_files_by_suffix(".md")
        resources_with_desc = []
        
        for name in resources:
            try:
                agent = await self.load_agent(agent_name=name)
                # Load the markdown content to parse description
                if agent:
                    desc = agent.desc() or "No description"
                    resources_with_desc.append((name, desc))
                else:
                    resources_with_desc.append((name, "No description"))
            except Exception as e:
                resources_with_desc.append((name, "No description"))
        
        return resources_with_desc

    async def load_as_source(self, name: str, version: str = None) -> Optional[str]:
        """
        Load agent configuration as source content (markdown format).
        
        Args:
            name: Agent name            version: Optional version number, if None uses the latest version
        
        Returns:
            Agent configuration markdown content, or None if not found
        """
        return await self._load_as_source_by_suffix(
            name=name,
            suffix=".md",
            version=version
        )

    async def load_agent(self, agent_name: str, version: str = None) -> Optional[Agent]:
        if not version:
            versions = await self.list_versions(name=agent_name)
            if not versions:
                return None
            version = versions[-1]

        # Get base_path and storage configuration
        base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))
        storage_type = self._get_storage_type()
        
        # Prepare OSS configuration (if needed)
        oss_config = self._get_oss_config()

        # Call static method to load agent
        agent = await self.get_agent_from_base_path(
            base_path=base_path,
            agent_name=agent_name,
            version=version,
            storage_type=storage_type,
            oss_config=oss_config
        )
        return agent


    async def save_as_source(self, content: str, name: str) -> bool:
        """Save configuration content as markdown file to storage base path."""
        return await self._save_file_by_suffix(
            content=content,
            name=name,
            suffix=".md",
        )



class AgentCodeVersionControlRegistry(VersionControlRegistry):
    """Version control registry for Python code agents with @agent decorator."""
    
    def __init__(self, context):
        VersionControlRegistry.__init__(self, context)
        self._lock = RLock()


    def _has_agent_decorator_fast(self, file_path: Path) -> bool:
        """
        Efficiently check if a Python file contains @agent decorator.
        Only reads the first max_lines of the file for performance.

        Args:
            file_path: Path to the Python file
            max_lines: Maximum number of lines to read (default: 100)

        Returns:
            True if file contains @agent decorator, False otherwise
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                # Only read first max_lines for efficiency
                for i, line in enumerate(f):
                    # Check for @agent decorator (with or without parentheses)
                    if '@agent' in line or '@agent(' in line:
                        return True
            return False
        except Exception as e:
            # If we can't read the file, assume it doesn't have the decorator
            return False

    def _matches_file(self, attachment: ArtifactAttachment, name: str, suffix: str, base_path: str = None) -> bool:
        """
        Check if an attachment matches the resource name and suffix.
        Overrides base class to implement content-based matching for Python files.
        For .py files, checks if file contains @agent decorator.
        For other files, uses default filename matching.
        
        Args:
            attachment: The attachment to check
            name: Resource name
            suffix: File suffix
            base_path: Base path for file access
        
        Returns:
            True if the file matches, False otherwise
        """
        # For non-Python files, use default filename matching
        if suffix != ".py":
            return super()._matches_file(attachment, name, suffix, base_path)
        
        # For Python files, check filename first
        if not super()._matches_file(attachment, name, suffix, base_path):
            return False
        
        # Then check file content for @agent decorator
        if base_path:
            file_path = Path(base_path) / attachment.path
            if file_path.exists():
                return self._has_agent_decorator_fast(file_path)
        
        return False

    def _scan_files_by_suffix(self, suffix: str) -> List[str]:
        """
        Scan files by suffix and extract resource names.
        For .py files, only include files that contain @agent decorator.
        Uses _matches_file method which implements content-based matching for Python files.
        
        Args:
            suffix: File suffix (e.g., ".py")
        
        Returns:
            Sorted list of resource names
        """
        # Use parent implementation which now uses _matches_file
        # _matches_file is overridden in this class to check for @agent decorator
        return super()._scan_files_by_suffix(suffix)

    async def get_agent_from_base_path(
        self,
        base_path: str,
        agent_name: str,
        version: str = None,
        storage_type: str = "local",
        oss_config: Optional[Dict[str, str]] = None
    ) -> Optional[Agent]:
        """
        Static method to load Python agent from base_path.
        
        Args:
            base_path: Base path of the agent registry
            agent_name: Agent name
            version: Agent version, if None uses the latest version
            storage_type: Storage type, "local" or "oss", defaults to "local"
            oss_config: OSS configuration dictionary containing access_key_id, access_key_secret, endpoint, bucket_name
        
        Returns:
            Agent instance, or None if not found
        """
        try:
            import sys
            import importlib.util
            
            # Create DirArtifact
            if storage_type == 'oss' and oss_config:
                dir_artifact = DirArtifact.with_oss_repository(
                    access_key_id=oss_config.get('access_key_id'),
                    access_key_secret=oss_config.get('access_key_secret'),
                    endpoint=oss_config.get('endpoint'),
                    bucket_name=oss_config.get('bucket_name'),
                    base_path=base_path
                )
            else:
                dir_artifact = DirArtifact.with_local_repository(base_path)
            
            # Use VersionControlRegistry static method to load resource
            attachment = await VersionControlRegistry.resolve_resource_from_artifact(
                dir_artifact=dir_artifact,
                name=agent_name,
                suffix=".py",
                version=version
            )
            
            if not attachment:
                return None
            
            # Get file path
            file_path = Path(dir_artifact.base_path) / attachment.path
            
            if not file_path.exists():
                logger.error(f"Python agent file not found: {file_path}")
                return None
            
            # Check if file contains @agent decorator
            if not self._has_agent_decorator_fast(file_path):
                return None
            
            # Load Python module
            project_root = file_path.parent
            project_root_str = str(project_root.absolute())
            
            # Add project root to sys.path if not already there
            if project_root_str not in sys.path:
                sys.path.insert(0, project_root_str)
            
            # Calculate module name
            module_name = file_path.stem
            
            # Use importlib to load the module
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                logger.error(f"Could not create spec for {file_path}")
                return None
            
            module = importlib.util.module_from_spec(spec)
            
            # Execute the module to trigger decorator registration
            spec.loader.exec_module(module)
            
            # Get agent from LocalAgentRegistry
            from aworld_cli.core.agent_registry import LocalAgentRegistry
            agents = LocalAgentRegistry.list_agents()
            
            if not agents:
                return None
            
            # Get the most recently registered agent (likely from this file)
            local_agent = agents[-1]
            
            if local_agent:
                swarm = await local_agent.get_swarm()
                if swarm and swarm.agents:
                    # Return the first agent in the swarm
                    agent_id, agent = next(iter(swarm.agents.items()))
                    return agent
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to load Python agent from base_path {base_path}: {e}")
            return None

    async def list_versions(self, name: str) -> List[str]:
        """List all versions of a resource by scanning .py files."""
        return self._list_versions_by_suffix(name=name, suffix=".py")

    async def list_as_source(self) -> List[str]:
        """List all available resources by scanning .py files (with @agent decorator)."""
        return self._scan_files_by_suffix(".py")

    async def list_desc(self) -> List[tuple]:
        """
        List all available resources with their descriptions.
        
        Args:        
        Returns:
            List of tuples (name, description) for each resource
        """
        resources = self._scan_files_by_suffix(".py")
        resources_with_desc = []
        
        # Try to get descriptions from LocalAgentRegistry first
        from aworld_cli.core.agent_registry import LocalAgentRegistry
        import os
        base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))
        local_agents_dict = {}
        local_agents_by_dir = {}
        try:
            local_agents = LocalAgentRegistry.list_agents()
            for local_agent in local_agents:
                if local_agent.name:
                    # Map by name (exact match)
                    local_agents_dict[local_agent.name] = local_agent.desc or "No description"
                    # Map by directory path (for matching resource names from directory structure)
                    if local_agent.register_dir:
                        # Extract directory name from register_dir
                        dir_name = os.path.basename(local_agent.register_dir.rstrip('/'))
                        if dir_name:
                            local_agents_by_dir[dir_name] = local_agent.desc or "No description"
                    # Also try to match by checking if resource name directory contains agent file
                    # Resource name is typically the directory name in base_path
                    resource_dir = os.path.join(base_path, local_agent.name)
                    if os.path.exists(resource_dir):
                        local_agents_by_dir[local_agent.name] = local_agent.desc or "No description"
        except Exception:
            pass
        
        for name in resources:
            try:
                desc = None
                # First try exact name match
                if name in local_agents_dict:
                    desc = local_agents_dict[name] or "No description"
                # Then try directory name match
                elif name in local_agents_by_dir:
                    desc = local_agents_by_dir[name] or "No description"
                # Fallback to loading agent and getting description from agent instance
                if not desc:
                    agent = await self.load_agent(agent_name=name)
                    if agent:
                        desc = agent.desc() or "No description"
                    else:
                        desc = "No description"
                resources_with_desc.append((name, desc))
            except Exception as e:
                resources_with_desc.append((name, "No description"))
        
        return resources_with_desc

    async def load_as_source(self, name: str, version: str = None) -> Optional[str]:
        """
        Load agent configuration as source content (Python code format).
        
        Args:
            name: Agent name            version: Optional version number, if None uses the latest version
        
        Returns:
            Agent Python source code content, or None if not found
        """
        return await self._load_as_source_by_suffix(
            name=name,
            suffix=".py",
            version=version
        )

    async def load_agent(self, agent_name: str, version: str = None) -> Optional[Agent]:
        if not version:
            versions = await self.list_versions(name=agent_name)
            if not versions:
                return None
            version = versions[-1]

        # Get base_path and storage configuration
        base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))
        storage_type = self._get_storage_type()
        
        # Prepare OSS configuration (if needed)
        oss_config = self._get_oss_config()

        # Call static method to load agent
        agent = await self.get_agent_from_base_path(
            base_path=base_path,
            agent_name=agent_name,
            version=version,
            storage_type=storage_type,
            oss_config=oss_config
        )
        return agent

    async def save_as_source(self, content: str, name: str) -> bool:
        """Save configuration content as Python file to storage base path."""
        return await self._save_file_by_suffix(
            content=content,
            name=name,
            suffix=".py",
            mime_type='text/x-python'
        )



class AgentVersionControlRegistry(VersionControlRegistry):
    """
    Unified version control registry that combines both DSL (markdown) and Code (Python) agents.
    It delegates to both AgentDslVersionControlRegistry and AgentCodeVersionControlRegistry.
    """
    
    def __init__(self, context):
        VersionControlRegistry.__init__(self, context)
        self._lock = RLock()
        # Initialize both registries
        self._dsl_registry = AgentDslVersionControlRegistry(context)
        self._code_registry = AgentCodeVersionControlRegistry(context)

    async def list_versions(self, name: str) -> List[str]:
        """
        List all versions of a resource by checking both DSL and Code registries.
        Merges versions from both registries and removes duplicates.
        """
        # Get versions from both registries
        dsl_versions = await self._dsl_registry.list_versions(name)
        code_versions = await self._code_registry.list_versions(name)
        
        # Merge and deduplicate versions
        all_versions = list(set(dsl_versions + code_versions))
        
        # Sort versions by version number
        def extract_version_number(v: str) -> int:
            match = re.match(r'v(\d+)', v)
            return int(match.group(1)) if match else 0
        
        all_versions.sort(key=extract_version_number)
        return all_versions

    async def list_as_source(self) -> List[str]:
        """
        List all available resources by combining results from both DSL and Code registries.
        """
        # Get resources from both registries
        dsl_resources = await self._dsl_registry.list_as_source()
        code_resources = await self._code_registry.list_as_source()
        
        # Merge and deduplicate
        all_resources = sorted(list(set(dsl_resources + code_resources)))
        return all_resources

    async def list_desc(self) -> List[tuple]:
        """
        List all available resources with their descriptions from both registries.
        """
        # Get descriptions from both registries
        dsl_desc = await self._dsl_registry.list_desc()
        code_desc = await self._code_registry.list_desc()
        
        # Merge results, using a dict to handle duplicates (prefer DSL if both exist)
        resources_dict = {}
        for name, desc in dsl_desc:
            resources_dict[name] = desc
        for name, desc in code_desc:
            # Only add if not already in dict (DSL takes precedence)
            if name not in resources_dict:
                resources_dict[name] = desc
        
        # Convert to sorted list of tuples
        return sorted([(name, desc) for name, desc in resources_dict.items()])

    async def load_as_source(self, name: str, version: str = None, session_id: str = None) -> Optional[str]:
        """
        Load agent configuration as source content.
        Tries DSL registry first, then Code registry.
        
        Args:
            name: Agent name
            version: Optional version number, if None uses the latest version
            session_id: Optional session ID (currently unused, kept for compatibility)
        """
        # Try DSL registry first
        content = await self._dsl_registry.load_as_source(name, version)
        if content:
            return content
        
        # Try Code registry
        content = await self._code_registry.load_as_source(name, version)
        return content

    async def load_agent(self, agent_name: str, version: str = None) -> Optional[Agent]:
        """
        Load agent by trying both registries.
        Tries DSL registry first, then Code registry.
        """
        # Try DSL registry first
        agent = await self._dsl_registry.load_agent(agent_name, version)
        if agent:
            return agent

        # Try Code registry
        agent = await self._code_registry.load_agent(agent_name, version)
        if agent:
            return agent

        return None

    async def save_as_source(self, content: str, name: str, 
                            registry_type: str = "dsl") -> bool:
        """
        Save configuration content to the specified registry.
        
        Args:
            content: Content to save
            name: Agent name
            registry_type: "dsl" for markdown or "code" for Python (default: "dsl")
        
        Returns:
            True if save successful, False otherwise
        """
        if registry_type == "code":
            return await self._code_registry.save_as_source(content, name)
        else:
            return await self._dsl_registry.save_as_source(content, name)


class DefaultContext:
    """Default context for AgentVersionControlRegistry when no context is provided."""

# Default instance of AgentVersionControlRegistry (unified)
global_agent_registry = AgentVersionControlRegistry(DefaultContext())
