# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import re
from pathlib import Path
from threading import RLock
from typing import Optional, Dict, List

from aworld.agents.llm_agent import Agent
from aworld.core.context.amni import DirArtifact
from aworld.core.context.amni.cvcs.version_control_registry import VersionControlRegistry
from aworld.experimental.aworld_cli.core.agent_registry import LocalAgent
from aworld.experimental.aworld_cli.core.markdown_agent_loader import parse_markdown_agent
from aworld.logs.util import logger


class AgentVersionControlRegistry(VersionControlRegistry):
    def __init__(self, context):
        VersionControlRegistry.__init__(self, context)
        self._lock = RLock()
        # Cache for loaded agents per session
        self._agent_cache: Dict[str, Dict[str, Dict[str, LocalAgent]]] = {}

    @staticmethod
    async def get_agent_from_base_path(
        base_path: str,
        agent_name: str,
        session_id: str = "default",
        version: str = None,
        storage_type: str = "local",
        oss_config: Optional[Dict[str, str]] = None
    ) -> Optional[Agent]:
        """
        Static method to load agent from base_path.
        
        Args:
            base_path: Base path of the agent registry
            agent_name: Agent name
            session_id: Session ID, defaults to "default"
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
                session_id=session_id,
                version=version
            )
            
            if not attachment:
                return None
            
            # Parse markdown agent
            file_path = Path(dir_artifact.base_path + '/' + attachment.path)
            local_agent = parse_markdown_agent(file_path)
            
            if local_agent:
                logger.debug(f"Loaded agent {agent_name} from base_path: {base_path}")
                swarm = await local_agent.get_swarm()
                if swarm and swarm.agents:
                    # Return the first agent in the swarm
                    agent_id, agent = next(iter(swarm.agents.items()))
                    return agent
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to load agent from base_path {base_path}: {e}")
            return None

    async def list_versions(self, name: str, session_id: str) -> List[str]:
        """List all versions of a resource by scanning .md files."""
        return self._list_versions_by_suffix(name=name, suffix=".md", session_id=session_id)

    async def list_as_source(self, session_id: str = None) -> List[str]:
        """List all available resources by scanning .md files."""
        return self._scan_files_by_suffix(".md")

    async def list_desc(self, session_id: str = None) -> List[tuple]:
        """
        List all available resources with their descriptions.
        
        Args:
            session_id: Optional session ID
        
        Returns:
            List of tuples (name, description) for each resource
        """
        resources = self._scan_files_by_suffix(".md")
        resources_with_desc = []
        
        for name in resources:
            try:
                agent = await self.load_agent(agent_name=name, session_id=session_id)
                # Load the markdown content to parse description
                if agent:
                    desc = agent.desc() or "No description"
                    resources_with_desc.append((name, desc))
                else:
                    resources_with_desc.append((name, "No description"))
            except Exception as e:
                logger.warning(f"Failed to load description for {name}: {e}")
                resources_with_desc.append((name, "No description"))
        
        return resources_with_desc

    async def load_as_source(self, name: str, session_id: str = None, version: str = None) -> Optional[str]:
        """
        Load agent configuration as source content (markdown format).
        
        Args:
            name: Agent name
            session_id: Optional session ID
            version: Optional version number, if None uses the latest version
        
        Returns:
            Agent configuration markdown content, or None if not found
        """
        return await self._load_as_source_by_suffix(
            name=name,
            suffix=".md",
            session_id=session_id,
            version=version
        )

    async def load_agent(self, agent_name: str, session_id: str = None, version: str = None) -> Optional[Agent]:
        session_id = self._get_session_id(session_id)

        # Check cache
        if session_id in self._agent_cache:
            if agent_name in self._agent_cache[session_id]:
                if version:
                    if version in self._agent_cache[session_id][agent_name]:
                        return self._agent_cache[session_id][agent_name][version]
                else:
                    versions = await self.list_versions(name=agent_name, session_id=session_id)
                    if versions:
                        latest_version = versions[-1]
                        if latest_version in self._agent_cache[session_id][agent_name]:
                            return self._agent_cache[session_id][agent_name][latest_version]

        if not version:
            versions = await self.list_versions(name=agent_name, session_id=session_id)
            if not versions:
                return None
            version = versions[-1]

        # Get base_path and storage configuration
        base_path = self._get_storage_base_path()
        storage_type = self._get_storage_type()
        
        # Prepare OSS configuration (if needed)
        oss_config = self._get_oss_config()

        # Call static method to load agent
        agent = await AgentVersionControlRegistry.get_agent_from_base_path(
            base_path=base_path,
            agent_name=agent_name,
            session_id=session_id,
            version=version,
            storage_type=storage_type,
            oss_config=oss_config
        )

        # Cache agent
        if agent:
            if session_id not in self._agent_cache:
                self._agent_cache[session_id] = {}
            if agent_name not in self._agent_cache[session_id]:
                self._agent_cache[session_id][agent_name] = {}
            self._agent_cache[session_id][agent_name][version] = agent

        return agent


    async def save_as_source(self, content: str, name: str, session_id: str = None) -> bool:
        """Save configuration content as markdown file to storage base path."""
        return await self._save_file_by_suffix(
            content=content,
            name=name,
            suffix=".md",
            session_id=session_id
        )
