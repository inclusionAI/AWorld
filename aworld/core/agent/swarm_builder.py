# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Swarm builder from YAML configuration.

This module provides utilities to build Swarm instances from YAML configuration files,
supporting nested swarms, parallel/serial agent groups, and flexible topology definitions.
"""

import yaml
from typing import Dict, List, Any, Union, Optional
from pathlib import Path

from aworld.core.agent.base import BaseAgent
from aworld.core.agent.swarm import (
    Swarm, WorkflowSwarm, TeamSwarm, HandoffSwarm,
    GraphBuildType
)
from aworld.core.exceptions import AWorldRuntimeException
from aworld.logs.util import logger


class SwarmConfigValidator:
    """Validator for swarm YAML configuration."""
    
    VALID_SWARM_TYPES = {"workflow", "handoff", "team"}
    VALID_NODE_TYPES = {"agent", "parallel", "serial", "swarm"}
    
    @staticmethod
    def validate_config(config: Dict[str, Any]) -> None:
        """Validate the swarm configuration.
        
        Args:
            config: Parsed YAML configuration dictionary.
            
        Raises:
            AWorldRuntimeException: If configuration is invalid.
        """
        if "swarm" not in config:
            raise AWorldRuntimeException("Missing 'swarm' key in configuration")
        
        swarm_config = config["swarm"]
        
        # Validate swarm type
        swarm_type = swarm_config.get("type")
        if not swarm_type:
            raise AWorldRuntimeException("Missing 'type' in swarm configuration")
        if swarm_type not in SwarmConfigValidator.VALID_SWARM_TYPES:
            raise AWorldRuntimeException(
                f"Invalid swarm type: {swarm_type}. "
                f"Must be one of {SwarmConfigValidator.VALID_SWARM_TYPES}"
            )
        
        # Validate agents
        agents = swarm_config.get("agents", [])
        if not agents:
            raise AWorldRuntimeException("Swarm must have at least one agent")
        
        # Validate agent definitions
        agent_ids = set()
        for agent_def in agents:
            if "id" not in agent_def:
                raise AWorldRuntimeException("Each agent must have an 'id' field")
            
            agent_id = agent_def["id"]
            if agent_id in agent_ids:
                raise AWorldRuntimeException(f"Duplicate agent id: {agent_id}")
            agent_ids.add(agent_id)
            
            node_type = agent_def.get("node_type", "agent")
            if node_type not in SwarmConfigValidator.VALID_NODE_TYPES:
                raise AWorldRuntimeException(
                    f"Invalid node_type: {node_type} for agent: {agent_id}. "
                    f"Must be one of {SwarmConfigValidator.VALID_NODE_TYPES}"
                )
            
            # Validate specific node types
            if node_type in ["parallel", "serial"]:
                if "agents" not in agent_def:
                    raise AWorldRuntimeException(
                        f"{node_type} node must have 'agents' field: {agent_id}"
                    )
            
            if node_type == "swarm":
                if "swarm_type" not in agent_def:
                    raise AWorldRuntimeException(
                        f"Swarm node must have 'swarm_type' field: {agent_id}"
                    )
                if "agents" not in agent_def:
                    raise AWorldRuntimeException(
                        f"Swarm node must have 'agents' field: {agent_id}"
                    )
                # Recursively validate nested swarm
                nested_config = {"swarm": {
                    "type": agent_def["swarm_type"],
                    "agents": agent_def["agents"],
                    "root_agent": agent_def.get("root_agent"),
                }}
                SwarmConfigValidator.validate_config(nested_config)
        
        # Validate edges if present
        edges = swarm_config.get("edges", [])
        for edge in edges:
            if "from" not in edge or "to" not in edge:
                raise AWorldRuntimeException("Each edge must have 'from' and 'to' fields")


class SwarmYAMLBuilder:
    """Builder to construct Swarm from YAML configuration."""
    
    def __init__(self, config: Dict[str, Any], agents_dict: Dict[str, BaseAgent]):
        """Initialize the builder.
        
        Args:
            config: Parsed YAML configuration dictionary.
            agents_dict: Dictionary mapping agent IDs to agent instances.
        """
        self.config = config
        self.agents_dict = agents_dict
        self.swarm_config = config["swarm"]
        
        # Validate configuration
        SwarmConfigValidator.validate_config(config)
        
        # Track created parallel/serial/nested agents
        self.created_agents: Dict[str, BaseAgent] = {}
    
    def build(self) -> Swarm:
        """Build the Swarm instance from configuration.
        
        Returns:
            Constructed Swarm instance.
        """
        swarm_type = self.swarm_config["type"]
        swarm_name = self.swarm_config.get("name", f"{swarm_type}_swarm")
        max_steps = self.swarm_config.get("max_steps", 0)
        event_driven = self.swarm_config.get("event_driven", True)
        root_agent_id = self.swarm_config.get("root_agent")
        
        # Build topology
        topology = self._build_topology()
        
        # Get root agent
        root_agent = self._get_root_agent(root_agent_id)
        
        # Create appropriate swarm type
        swarm_cls = self._get_swarm_class(swarm_type)
        
        kwargs = {
            "topology": topology,
            "root_agent": root_agent,
            "max_steps": max_steps,
            "event_driven": event_driven,
            "name": swarm_name,
        }
        
        # Add team-specific parameters
        if swarm_type == "team":
            kwargs["min_call_num"] = self.swarm_config.get("min_call_num", 0)
        
        return swarm_cls(**kwargs)
    
    def _get_swarm_class(self, swarm_type: str):
        """Get the appropriate Swarm class based on type."""
        mapping = {
            "workflow": WorkflowSwarm,
            "handoff": HandoffSwarm,
            "team": TeamSwarm,
        }
        return mapping[swarm_type]
    
    def _build_topology(self) -> List[tuple]:
        """Build the topology list from configuration.
        
        Returns:
            List of agent pairs (tuples) or single agents defining the topology.
        """
        agents_config = self.swarm_config["agents"]
        edges_config = self.swarm_config.get("edges", [])
        
        # First pass: create all agents (including parallel/serial/nested)
        for agent_def in agents_config:
            self._create_agent_if_needed(agent_def)
        
        # Build edges from 'next' syntax sugar
        next_edges = []
        for agent_def in agents_config:
            agent_id = agent_def["id"]
            next_ids = agent_def.get("next")
            
            if next_ids:
                # Normalize to list
                if not isinstance(next_ids, list):
                    next_ids = [next_ids]
                
                for next_id in next_ids:
                    next_edges.append((agent_id, next_id))
        
        # Build edges from explicit 'edges' definition
        explicit_edges = []
        for edge in edges_config:
            explicit_edges.append((edge["from"], edge["to"]))
        
        # Merge edges: explicit edges have higher priority (remove duplicates from next_edges)
        explicit_edges_set = set(explicit_edges)
        merged_edges = explicit_edges.copy()
        for edge in next_edges:
            if edge not in explicit_edges_set:
                merged_edges.append(edge)
        
        # Convert edge tuples to agent pairs
        topology = []
        for from_id, to_id in merged_edges:
            from_agent = self._get_agent_by_id(from_id)
            to_agent = self._get_agent_by_id(to_id)
            topology.append((from_agent, to_agent))
        
        # For workflow without explicit edges, add single agents
        if not topology and self.swarm_config["type"] == "workflow":
            for agent_def in agents_config:
                agent = self._get_agent_by_id(agent_def["id"])
                topology.append(agent)
        
        return topology if topology else [self._get_agent_by_id(agents_config[0]["id"])]
    
    def _create_agent_if_needed(self, agent_def: Dict[str, Any]) -> None:
        """Create special agent types (parallel/serial/swarm) if needed.
        
        Args:
            agent_def: Agent definition from configuration.
        """
        agent_id = agent_def["id"]
        node_type = agent_def.get("node_type", "agent")
        
        if node_type == "agent":
            # Regular agent, should exist in agents_dict
            if agent_id not in self.agents_dict:
                raise AWorldRuntimeException(
                    f"Agent '{agent_id}' not found in agents_dict"
                )
            return
        
        if agent_id in self.created_agents:
            # Already created
            return
        
        if node_type == "parallel":
            agent = self._create_parallel_agent(agent_def)
        elif node_type == "serial":
            agent = self._create_serial_agent(agent_def)
        elif node_type == "swarm":
            agent = self._create_nested_swarm(agent_def)
        else:
            raise AWorldRuntimeException(f"Unknown node_type: {node_type}")
        
        self.created_agents[agent_id] = agent
    
    def _create_parallel_agent(self, agent_def: Dict[str, Any]) -> BaseAgent:
        """Create a ParallelizableAgent from configuration.
        
        Args:
            agent_def: Agent definition for parallel group.
            
        Returns:
            ParallelizableAgent instance.
        """
        from aworld.agents.parallel_llm_agent import ParallelizableAgent
        
        agent_id = agent_def["id"]
        agent_ids = agent_def["agents"]
        
        # Get agent instances
        agents = []
        for aid in agent_ids:
            if aid in self.agents_dict:
                agents.append(self.agents_dict[aid])
            elif aid in self.created_agents:
                agents.append(self.created_agents[aid])
            else:
                raise AWorldRuntimeException(
                    f"Agent '{aid}' not found for parallel group '{agent_id}'"
                )
        
        name = agent_def.get("name", f"parallel_{agent_id}")
        return ParallelizableAgent(name=name, agents=agents)
    
    def _create_serial_agent(self, agent_def: Dict[str, Any]) -> BaseAgent:
        """Create a SerialableAgent from configuration.
        
        Args:
            agent_def: Agent definition for serial group.
            
        Returns:
            SerialableAgent instance.
        """
        from aworld.agents.serial_llm_agent import SerialableAgent
        
        agent_id = agent_def["id"]
        agent_ids = agent_def["agents"]
        
        # Get agent instances
        agents = []
        for aid in agent_ids:
            if aid in self.agents_dict:
                agents.append(self.agents_dict[aid])
            elif aid in self.created_agents:
                agents.append(self.created_agents[aid])
            else:
                raise AWorldRuntimeException(
                    f"Agent '{aid}' not found for serial group '{agent_id}'"
                )
        
        name = agent_def.get("name", f"serial_{agent_id}")
        return SerialableAgent(name=name, agents=agents)
    
    def _create_nested_swarm(self, agent_def: Dict[str, Any]) -> BaseAgent:
        """Create a nested Swarm wrapped in TaskAgent.
        
        Args:
            agent_def: Agent definition for nested swarm.
            
        Returns:
            TaskAgent wrapping the nested Swarm.
        """
        from aworld.agents.task_llm_agent import TaskAgent
        
        agent_id = agent_def["id"]
        swarm_type = agent_def["swarm_type"]
        
        # Build nested swarm configuration
        nested_config = {
            "swarm": {
                "type": swarm_type,
                "name": agent_def.get("name", agent_id),
                "agents": agent_def["agents"],
                "root_agent": agent_def.get("root_agent"),
                "max_steps": agent_def.get("max_steps", 0),
                "event_driven": agent_def.get("event_driven", True),
                "edges": agent_def.get("edges", []),
            }
        }
        
        # Recursively build nested swarm
        nested_builder = SwarmYAMLBuilder(nested_config, self.agents_dict)
        nested_swarm = nested_builder.build()
        
        # Wrap in TaskAgent
        task_agent_name = agent_def.get("name", f"swarm_{swarm_type}_{agent_id}")
        return TaskAgent(name=task_agent_name, swarm=nested_swarm)
    
    def _get_agent_by_id(self, agent_id: str) -> BaseAgent:
        """Get agent instance by ID.
        
        Args:
            agent_id: Agent identifier.
            
        Returns:
            Agent instance.
            
        Raises:
            AWorldRuntimeException: If agent not found.
        """
        if agent_id in self.agents_dict:
            return self.agents_dict[agent_id]
        if agent_id in self.created_agents:
            return self.created_agents[agent_id]
        raise AWorldRuntimeException(f"Agent '{agent_id}' not found")
    
    def _get_root_agent(self, root_agent_id: Optional[Union[str, List[str]]]) -> Optional[Union[BaseAgent, List[BaseAgent]]]:
        """Get root agent(s) from configuration.
        
        Args:
            root_agent_id: Root agent ID or list of IDs.
            
        Returns:
            Root agent instance or list of instances, or None.
        """
        if not root_agent_id:
            return None
        
        if isinstance(root_agent_id, list):
            return [self._get_agent_by_id(aid) for aid in root_agent_id]
        else:
            return self._get_agent_by_id(root_agent_id)


def build_swarm_from_yaml(
    yaml_path: str,
    agents_dict: Dict[str, BaseAgent],
    **kwargs
) -> Swarm:
    """Build a Swarm instance from YAML configuration file.
    
    Args:
        yaml_path: Path to YAML configuration file.
        agents_dict: Dictionary mapping agent IDs to agent instances.
                    Note: For nested swarms, must include agents from all levels.
        **kwargs: Additional parameters to override YAML configuration.
    
    Returns:
        Constructed Swarm instance.
    
    Raises:
        AWorldRuntimeException: If configuration is invalid or agents are missing.
    
    Example:
        >>> from aworld.core.agent.base import Agent
        >>> from aworld.core.agent.swarm_builder import build_swarm_from_yaml
        >>> 
        >>> agents = {
        >>>     "agent1": Agent(name="agent1"),
        >>>     "agent2": Agent(name="agent2"),
        >>>     "agent3": Agent(name="agent3"),
        >>> }
        >>> swarm = build_swarm_from_yaml("config.yaml", agents)
    """
    # Load YAML file
    yaml_file = Path(yaml_path)
    if not yaml_file.exists():
        raise AWorldRuntimeException(f"YAML file not found: {yaml_path}")
    
    with open(yaml_file, 'r', encoding='utf-8') as f:
        try:
            config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise AWorldRuntimeException(f"Failed to parse YAML file: {e}")
    
    if not config:
        raise AWorldRuntimeException("Empty YAML configuration")
    
    # Build swarm
    builder = SwarmYAMLBuilder(config, agents_dict)
    swarm = builder.build()
    
    # Apply kwargs overrides
    for key, value in kwargs.items():
        if hasattr(swarm, key):
            setattr(swarm, key, value)
        else:
            logger.warning(f"Unknown parameter '{key}' in kwargs, ignoring")
    
    return swarm


def build_swarm_from_dict(
    config: Dict[str, Any],
    agents_dict: Dict[str, BaseAgent],
    **kwargs
) -> Swarm:
    """Build a Swarm instance from configuration dictionary.
    
    Args:
        config: Configuration dictionary (same structure as YAML).
        agents_dict: Dictionary mapping agent IDs to agent instances.
        **kwargs: Additional parameters to override configuration.
    
    Returns:
        Constructed Swarm instance.
    
    Raises:
        AWorldRuntimeException: If configuration is invalid or agents are missing.
    """
    builder = SwarmYAMLBuilder(config, agents_dict)
    swarm = builder.build()
    
    # Apply kwargs overrides
    for key, value in kwargs.items():
        if hasattr(swarm, key):
            setattr(swarm, key, value)
        else:
            logger.warning(f"Unknown parameter '{key}' in kwargs, ignoring")
    
    return swarm
