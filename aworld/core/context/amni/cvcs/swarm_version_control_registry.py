# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from typing import Dict, Tuple, Optional, List, Any

import yaml

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm, GraphBuildType
from aworld.core.context.amni import DirArtifact
from aworld.core.context.amni.cvcs.agent_version_control_registry import AgentVersionControlRegistry
from aworld.core.context.amni.cvcs.version_control_registry import VersionControlRegistry
from aworld.logs.util import logger


class SwarmVersionControlRegistry(VersionControlRegistry):
    """
    Swarm registry service, responsible for loading and managing swarm configurations.
    """



    @staticmethod
    async def load_swarm_source_from_base_path(
        base_path: str,
        team_name: str,
        session_id: str = "default",
        storage_type: str = "local",
        oss_config: Optional[Dict[str, str]] = None,
        version: str = None
    ) -> Tuple[Optional[str], Dict[str, str]]:
        """
        Static method to load swarm configuration source code from base_path.
        
        Args:
            base_path: Base path of the agent registry
            team_name: Team name, corresponding YAML file name is {team_name}.yaml
            session_id: Session ID, defaults to "default"
            storage_type: Storage type, "local" or "oss", defaults to "local"
            oss_config: OSS configuration dictionary containing access_key_id, access_key_secret, endpoint, bucket_name
            version: Optional version number
        
        Returns:
            Tuple of (swarm_content, agents_content)
            swarm_content: Swarm configuration YAML content string, or None if not found
            agents_content: Dictionary containing all related Agent source code {agent_name: source_content}
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
            session_id=session_id,
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
                        session_id=session_id,
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
        session_id: str = "default",
        storage_type: str = "local",
        oss_config: Optional[Dict[str, str]] = None
    ) -> Tuple[Swarm, Dict[str, Agent]]:
        """
        Static method to load swarm configuration from base_path.
        
        Args:
            base_path: Base path of the agent registry
            team_name: Team name, corresponding YAML file name is {team_name}.yaml
            session_id: Session ID, defaults to "default"
            storage_type: Storage type, "local" or "oss", defaults to "local"
            oss_config: OSS configuration dictionary containing access_key_id, access_key_secret, endpoint, bucket_name
        
        Returns:
            Tuple of (swarm, agents_dict)
            If `swarm` section is missing, builds a default workflow based on agent names in YAML.
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
            session_id=session_id,
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
                    session_id=session_id,
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
                    session_id=session_id,
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
                    session_id=session_id,
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
                session_id=session_id,
                storage_type=storage_type,
                oss_config=oss_config
            )
            if agent is None:
                raise ValueError(f"Agent '{name}' not found in registry")
            agents[name] = agent
        
        ordered = [agents[root]] + [agents[m] for m in members if m != root]
        return Swarm(*ordered, build_type=GraphBuildType.TEAM), agents
    
    async def list_versions(self, name: str, session_id: str) -> List[str]:
        """List all versions of a resource by scanning .yaml files."""
        return self._list_versions_by_suffix(name=name, suffix=".yaml", session_id=session_id)
    
    async def list_as_source(self, session_id: str = None) -> List[str]:
        """List all available swarm resources by scanning .yaml files."""
        return self._scan_files_by_suffix(".yaml")

    async def save_as_source(self, content: str, name: str, session_id: str = None) -> bool:
        """Save configuration content as YAML file to storage base path."""
        return await self._save_file_by_suffix(
            content=content,
            name=name,
            suffix=".yaml",
            session_id=session_id
        )

    async def load_as_source(self, name: str, session_id: str = None, version: str = None) -> Optional[str]:
        """
        Load swarm configuration as source content (YAML format).

        Args:
            name: Team name, corresponding YAML file name is {name}.yaml
            session_id: Optional session ID (currently unused, reserved for future extension)
            version: Optional version number (currently unused, reserved for future extension)

        Returns:
            Swarm configuration YAML content, or None if not found
        """
        return await self._load_as_source_by_suffix(
            name=name,
            suffix=".yaml",
            session_id=session_id,
            version=version
        )

    async def load_swarm_and_agents_as_source(self, name: str, session_id: str = None, version: str = None) -> Tuple[Optional[str], Dict[str, str]]:
        """
        Load swarm configuration as source content (YAML format).
        
        Args:
            name: Team name, corresponding YAML file name is {name}.yaml
            session_id: Optional session ID
            version: Optional version number
        
        Returns:
            Tuple of (swarm_content, agents_content)
            swarm_content: Swarm configuration YAML content, or None if not found
            agents_content: Dictionary containing all related Agent source code {agent_name: source_content}
        """
        session_id = self._get_session_id(session_id)
        
        # Get base_path and storage configuration
        base_path = self._get_storage_base_path()
        storage_type = self._get_storage_type()
        
        # Prepare OSS configuration (if needed)
        oss_config = self._get_oss_config()

        return await SwarmVersionControlRegistry.load_swarm_source_from_base_path(
            base_path=base_path,
            team_name=name,
            session_id=session_id,
            storage_type=storage_type,
            oss_config=oss_config,
            version=version
        )
    
    async def load_swarm_and_agents(self, team_name: str, session_id: str = None) -> Tuple[Swarm, Dict[str, Agent]]:
        """
        Load swarm configuration from YAML file, using get_agent_from_base_path method to load agents.

        Args:
            team_name: Team name, corresponding YAML file name is {team_name}.yaml
            session_id: Optional session ID

        Returns:
            Tuple of (swarm, agents_dict)
            If `swarm` section is missing, builds a default workflow based on agent names in YAML.
        """
        session_id = self._get_session_id(session_id)

        # Get base_path and storage configuration
        base_path = self._get_storage_base_path()
        storage_type = self._get_storage_type()
        
        # Prepare OSS configuration (if needed)
        oss_config = self._get_oss_config()

        # Call static method to load swarm
        return await SwarmVersionControlRegistry.load_swarm_from_base_path(
            base_path=base_path,
            team_name=team_name,
            session_id=session_id,
            storage_type=storage_type,
            oss_config=oss_config
        )
