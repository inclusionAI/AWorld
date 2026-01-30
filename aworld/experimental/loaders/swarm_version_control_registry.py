# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from typing import Dict, Tuple, Optional, List, Any

import yaml

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm, GraphBuildType
from aworld.core.context.amni import DirArtifact
from aworld.experimental.loaders.agent_version_control_registry import AgentVersionControlRegistry
from aworld.experimental.loaders.version_control_registry import VersionControlRegistry
from aworld.logs.util import logger
from aworld.output.artifact import ArtifactAttachment


class SwarmVersionControlRegistry(VersionControlRegistry):
    """Swarm registry service, responsible for loading and managing swarm configurations."""

    def _list_versions_by_suffix(self, name: str, suffix: str) -> List[str]:
        """
        List all versions of a resource by suffix.
        
        Scanning rules:
        - v0 version: in top-level directory {name}/{name}{suffix}
        - v1+ versions: in top-level directory {name}/{name}_vN{suffix}
        """
        import re
        versions = []
        self._dir_artifact.reload_working_files()

        # Check for attachments matching the resource name
        if self._dir_artifact.attachments:
            # Check for root v0 file (name.yaml) in top-level directory
            v0_filename = f"{name}{suffix}"
            v0_path_prefix = f"{name}/"
            for attachment in self._dir_artifact.attachments:
                if (attachment.filename == v0_filename and 
                    attachment.path.startswith(v0_path_prefix) and
                    "/" not in attachment.path[len(v0_path_prefix):]):  # No subdirectory
                    versions.append("v0")
                    break

            # Check for versioned files (name_vN.yaml) in top-level directory (no session_id subdirectory)
            pattern = re.compile(rf"^{re.escape(name)}_v(\d+){re.escape(suffix)}$")
            for attachment in self._dir_artifact.attachments:
                match = pattern.match(attachment.filename)
                if match and attachment.path.startswith(v0_path_prefix):
                    # Check that there's no subdirectory (no session_id)
                    path_after_prefix = attachment.path[len(v0_path_prefix):]
                    if "/" not in path_after_prefix:  # No subdirectory means no session_id
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
        mime_type: str = None
    ) -> bool:
        """
        Save file to storage by suffix.
        
        First save (v0): save to top-level directory {name}/{name}{suffix}
        Subsequent saves (v1+): save to top-level directory {name}/{name}_vN{suffix}
        """
        try:
            from aworld.output.artifact import ArtifactAttachment

            # Generate new version number
            new_version = await self.generate_new_version(name=name)

            # Create filename and path based on version (no session_id in path)
            if new_version == "v0":
                # First save: save to top-level directory
                filename = f"{name}{suffix}"
                file_path = f"{name}/{filename}"
            else:
                # Subsequent saves: save to top-level directory (no session_id subdirectory)
                filename = f"{name}_{new_version}{suffix}"
                file_path = f"{name}/{filename}"

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
                return True
            else:
                logger.error(f"Failed to save file: {name}")
                return False

        except Exception as e:
            logger.error(f"Failed to save file: {e}")
            return False

    async def _load_content_by_suffix(self, name: str, suffix: str, version: str) -> Optional[str]:
        """
        Load resource content from storage by suffix.
        
        Loading rules:
        - v0 version: from top-level directory {name}/{name}{suffix}
        - v1+ versions: from top-level directory {name}/{name}_vN{suffix}
        """
        try:
            # Build filename and relative path (no session_id in path)
            if version == "v0":
                filename = f"{name}{suffix}"
                relative_path = f"{name}/{filename}"
            else:
                filename = f"{name}_{version}{suffix}"
                relative_path = f"{name}/{filename}"
            
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
                base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))
                path = os.path.join(base_path, relative_path)
                
                if not os.path.exists(path):
                    return None
                
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()

        except Exception as e:
            logger.error(f"Failed to load content using suffix {suffix}: {e}")
            return None

    @staticmethod
    async def resolve_resource_from_artifact(
        dir_artifact: DirArtifact,
        name: str,
        suffix: str,
        version: str = None
    ) -> Optional[ArtifactAttachment]:
        """
        Find resource in DirArtifact.
        If version is None, automatically find the latest version.
        
        Scanning rules:
        - v0 version: in top-level directory {name}/{name}{suffix}
        - v1+ versions: in top-level directory {name}/{name}_vN{suffix}
        """
        import re
        
        dir_artifact.reload_working_files()
        path_prefix = f"{name}/"

        # 1. Find latest version
        if not version:
            versions = []
            if dir_artifact.attachments:
                # Find v0 (in top-level directory)
                v0_filename = f"{name}{suffix}"
                for attachment in dir_artifact.attachments:
                    if (attachment.filename.endswith(v0_filename) and
                        attachment.path.startswith(path_prefix)):
                        path_after_prefix = attachment.path[len(path_prefix):]
                        if "/" not in path_after_prefix:  # No subdirectory
                            versions.append("v0")
                            break
                
                # Find versioned files (in top-level directory, no session_id subdirectory)
                pattern = re.compile(rf"^{re.escape(name)}_v(\d+){re.escape(suffix)}$")
                for attachment in dir_artifact.attachments:
                    match = pattern.match(attachment.filename)
                    if match and attachment.path.startswith(path_prefix):
                        path_after_prefix = attachment.path[len(path_prefix):]
                        if "/" not in path_after_prefix:  # No subdirectory means no session_id
                            version_str = f"v{match.group(1)}"
                            if version_str not in versions:  # Avoid duplicates
                                versions.append(version_str)

            
            if not versions:
                return None
            
            # Sort versions
            def extract_version_number(v: str) -> int:
                match = re.match(r'v(\d+)', v)
                return int(match.group(1)) if match else 0
            
            versions.sort(key=extract_version_number)
            version = versions[-1]
            
        # 2. Get file (no session_id in path)
        filename = f"{name}{suffix}" if version == "v0" else f"{name}_{version}{suffix}"
        path = path_prefix + filename
        return dir_artifact.get_file(path)

    @staticmethod
    async def load_swarm_source_from_base_path(
        base_path: str,
        team_name: str,
        storage_type: str = "local",
        oss_config: Optional[Dict[str, str]] = None,
        version: str = None
    ) -> Tuple[Optional[str], Dict[str, str]]:
        """
        Static method to load swarm configuration source code from base_path.
        """
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
        
        # Use VersionControlRegistry static method to load YAML resource
        attachment = await VersionControlRegistry.resolve_resource_from_artifact(
            dir_artifact=dir_artifact,
            name=team_name,
            suffix=".yaml",
            version=version
        )
        
        if not attachment:
            return None, {}
            
        swarm_content = attachment.content
        if isinstance(swarm_content, bytes):
            swarm_content = swarm_content.decode('utf-8')
        agents_content = {}
        
        try:
            # Parse YAML content to get referenced agents
            data = yaml.safe_load(swarm_content) or {}
            if isinstance(data, dict):
                agent_names = set()
                swarm_conf = data.get("swarm")
                
                if not swarm_conf:
                    # If no swarm configuration, try to get agent name list from agents section
                    agent_names.update(data.get("agents", {}).keys())
                else:
                    stype = (swarm_conf.get("type") or GraphBuildType.WORKFLOW.value).lower()
                    
                    if stype == GraphBuildType.WORKFLOW.value:
                        order = swarm_conf.get("order") or list(data.get("agents", {}).keys())
                        if isinstance(order, list):
                            agent_names.update(order)
                            
                    elif stype == GraphBuildType.HANDOFF.value:
                        edges = swarm_conf.get("edges") or []
                        for edge in edges:
                            if isinstance(edge, list) and len(edge) >= 2:
                                agent_names.add(edge[0])
                                agent_names.add(edge[1])
                                
                    elif stype == GraphBuildType.TEAM.value:
                        root = swarm_conf.get("root")
                        members = swarm_conf.get("members") or []
                        if not root:
                            root = next(iter(data.get("agents", {}).keys()), None)
                        if root:
                            agent_names.add(root)
                        if isinstance(members, list):
                            agent_names.update(members)
                
                # Load source for each agent
                for agent_name in agent_names:
                    agent_att = await VersionControlRegistry.resolve_resource_from_artifact(
                        dir_artifact=dir_artifact,
                        name=agent_name,
                        suffix=".md",
                        version=None
                    )
                    if agent_att:
                        content = agent_att.content
                        if isinstance(content, bytes):
                            content = content.decode('utf-8')
                        agents_content[agent_name] = content
                        
        except Exception as e:
            logger.warning(f"Failed to parse swarm source or load agents: {e}")
            
        return swarm_content, agents_content

    @staticmethod
    async def load_swarm_from_base_path(
        base_path: str,
        team_name: str,
        storage_type: str = "local",
        oss_config: Optional[Dict[str, str]] = None
    ) -> Tuple[Swarm, Dict[str, Agent]]:
        """
        Static method to load swarm configuration from base_path.
        """
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
        
        # Use VersionControlRegistry static method to load YAML resource
        attachment = await VersionControlRegistry.resolve_resource_from_artifact(
            dir_artifact=dir_artifact,
            name=team_name,
            suffix=".yaml",
            version=None
        )
        
        if not attachment:
            raise FileNotFoundError(f"Config YAML not found for team: {team_name}")
        
        # Parse YAML content
        data = yaml.safe_load(attachment.content) or {}
        
        if not isinstance(data, dict):
            raise ValueError("Top-level YAML must be a mapping (dict)")
        
        # Get swarm configuration
        swarm_conf: Optional[Dict[str, Any]] = data.get("swarm")
        
        # If no swarm configuration, try to get agent name list from agents section
        if not swarm_conf:
            # Default: build simple workflow in the order agents are declared
            agent_names = list(data.get("agents", {}).keys())
            if not agent_names:
                raise ValueError("No agents defined to build a swarm")
            
            # Use AgentVersionControlRegistry.get_agent_from_base_path to load each agent
            agents: Dict[str, Agent] = {}
            for name in agent_names:
                agent = await AgentVersionControlRegistry.get_agent_from_base_path(
                    base_path=base_path,
                    agent_name=name,
                    storage_type=storage_type,
                    oss_config=oss_config
                )
                if agent is None:
                    raise ValueError(f"Agent '{name}' not found in registry")
                agents[name] = agent
            
            ordered = [agents[name] for name in agent_names]
            return Swarm(*ordered), agents
        
        # Parse swarm type
        stype = (swarm_conf.get("type") or GraphBuildType.WORKFLOW.value).lower()
        if stype not in {GraphBuildType.WORKFLOW.value, GraphBuildType.HANDOFF.value, GraphBuildType.TEAM.value}:
            raise ValueError(f"Unsupported swarm.type: {stype}")
        
        # WORKFLOW type
        if stype == GraphBuildType.WORKFLOW.value:
            order: List[str] = swarm_conf.get("order") or list(data.get("agents", {}).keys())
            if not isinstance(order, list) or not order:
                raise ValueError("For workflow swarm, `order` must be a non-empty list of agent names")
            
            # Use AgentVersionControlRegistry.get_agent_from_base_path to load each agent
            agents: Dict[str, Agent] = {}
            for name in order:
                agent = await AgentVersionControlRegistry.get_agent_from_base_path(
                    base_path=base_path,
                    agent_name=name,
                    storage_type=storage_type,
                    oss_config=oss_config
                )
                if agent is None:
                    raise ValueError(f"Agent '{name}' not found in registry")
                agents[name] = agent
            
            ordered_agents = [agents[name] for name in order]
            return Swarm(*ordered_agents), agents
        
        # HANDOFF type
        if stype == GraphBuildType.HANDOFF.value:
            edges: List[List[str]] = swarm_conf.get("edges") or []
            if not edges:
                raise ValueError("For handoff swarm, `edges` must be provided as [[left, right], ...]")
            
            # Collect all required agent names
            agent_names_set = set()
            for a, b in edges:
                agent_names_set.add(a)
                agent_names_set.add(b)
            
            # Use AgentVersionControlRegistry.get_agent_from_base_path to load each agent
            agents: Dict[str, Agent] = {}
            for name in agent_names_set:
                agent = await AgentVersionControlRegistry.get_agent_from_base_path(
                    base_path=base_path,
                    agent_name=name,
                    storage_type=storage_type,
                    oss_config=oss_config
                )
                if agent is None:
                    raise ValueError(f"Agent '{name}' not found in registry")
                agents[name] = agent
            
            pairs = []
            for a, b in edges:
                pairs.append((agents[a], agents[b]))
            return Swarm(*pairs, build_type=GraphBuildType.HANDOFF), agents
        
        # TEAM type
        root: str = swarm_conf.get("root")
        members: List[str] = swarm_conf.get("members") or []
        
        if not root:
            # If root is not specified, default to the first defined agent
            root = next(iter(data.get("agents", {}).keys()), None)
        
        if not root:
            raise ValueError("For team swarm, `root` or at least one agent must be defined")
        
        # Collect all required agent names
        agent_names_set = {root}
        agent_names_set.update(members)
        
        # Use AgentVersionControlRegistry.get_agent_from_base_path to load each agent
        agents: Dict[str, Agent] = {}
        for name in agent_names_set:
            agent = await AgentVersionControlRegistry.get_agent_from_base_path(
                base_path=base_path,
                agent_name=name,
                storage_type=storage_type,
                oss_config=oss_config
            )
            if agent is None:
                raise ValueError(f"Agent '{name}' not found in registry")
            agents[name] = agent
        
        ordered = [agents[root]] + [agents[m] for m in members if m != root]
        return Swarm(*ordered, build_type=GraphBuildType.TEAM), agents
    
    async def list_versions(self, name: str) -> List[str]:
        """List all versions of a resource by scanning .yaml files."""
        return self._list_versions_by_suffix(name=name, suffix=".yaml")
    
    async def list_as_source(self) -> List[str]:
        """List all available swarm resources by scanning .yaml files."""
        return self._scan_files_by_suffix(".yaml")

    async def save_as_source(self, content: str, name: str) -> bool:
        """Save configuration content as YAML file to storage base path."""
        return await self._save_file_by_suffix(
            content=content,
            name=name,
            suffix=".yaml"
        )

    async def load_as_source(self, name: str, version: str = None) -> Optional[str]:
        """Load swarm configuration as source content (YAML format)."""
        return await self._load_as_source_by_suffix(
            name=name,
            suffix=".yaml",
            version=version
        )

    async def load_swarm_and_agents_as_source(self, name: str, version: str = None) -> Tuple[Optional[str], Dict[str, str]]:
        """Load swarm configuration as source content (YAML format)."""
        # Get base_path and storage configuration
        base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))
        storage_type = self._get_storage_type()
        
        # Prepare OSS configuration (if needed)
        oss_config = self._get_oss_config()

        return await SwarmVersionControlRegistry.load_swarm_source_from_base_path(
            base_path=base_path,
            team_name=name,
            storage_type=storage_type,
            oss_config=oss_config,
            version=version
        )
    
    async def load_swarm_and_agents(self, team_name: str) -> Tuple[Swarm, Dict[str, Agent]]:
        """Load swarm configuration from YAML file."""
        # Get base_path and storage configuration
        base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))
        storage_type = self._get_storage_type()
        
        # Prepare OSS configuration (if needed)
        oss_config = self._get_oss_config()

        # Call static method to load swarm
        return await SwarmVersionControlRegistry.load_swarm_from_base_path(
            base_path=base_path,
            team_name=team_name,
            storage_type=storage_type,
            oss_config=oss_config
        )


class DefaultContext:
    """Default context for SwarmVersionControlRegistry when no context is provided."""
    def __init__(self, session_id: str = "default"):
        self.session_id = session_id


# Default instance of SwarmVersionControlRegistry
_default_swarm_registry = SwarmVersionControlRegistry(DefaultContext())
