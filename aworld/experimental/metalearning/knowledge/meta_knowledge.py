# coding: utf-8
"""
Meta Knowledge Module

This module provides meta data processing functionality, mainly used for:
1. Extracting agent and tool information from trajectory data
2. Building and saving trajectory metadata
3. Retrieving saved trajectory metadata
"""

import json
from typing import Dict, Optional, Any

from aworld.core.agent.base import AgentFactory
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.base import Context
from aworld.dataset.types import TrajectoryItem
from aworld_cli.core.agent_scanner import global_agent_registry
from aworld.experimental.metalearning.knowledge.learning_knowledge import (
    AgentSnapshot,
    TrajType,
    get_context_artifact_data,
    save_context_artifact
)
from aworld.logs.util import logger


class MetaKnowledge:
    """Meta knowledge class, providing meta data processing functionality"""

    @staticmethod
    async def extract_agents_and_tools_from_item(context: ApplicationContext, item: TrajectoryItem, agents_config: dict):
        """
        Extract agents and tools from trajectory item
        
        Args:
            context: ApplicationContext object
            item: TrajectoryItem object
            agents_config: Dictionary for storing agent configuration
        """
        # Extract agent_id
        agent = AgentFactory.agent_instance(item.meta.agent_id)
        # Get class source code and instance member variable values, and code file path
        definition = await global_agent_registry.load_as_source(name=agent.name())
        # Version comparison is no longer supported, set diffs to None
        diffs = None
        agents_config[item.meta.agent_id] = AgentSnapshot(
            id=agent.id(),
            name=agent.name(),
            prompt=agent.system_prompt,
            definition=definition,
            diffs=diffs
        )

    @staticmethod
    async def get_running_meta(context: ApplicationContext, task_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get trajectory metadata information
        
        Query AgentTeam, all Agents and tools involved in the trajectory
        
        Args:
            context: ApplicationContext object
            task_id: Task ID, if None uses context.task_id
            
        Returns:
            Dictionary containing metadata, including:
            - task_id: Task ID
            - agents: Dictionary of all agents involved in trajectory
            - agent_count: Number of agents
        """
        if task_id is None:
            task_id = context.task_id

        # Query task graph
        task_graph = context.get_task_graph()

        agents_config = {}

        # Handle multi-task scenario: if task graph exists and contains multiple nodes, read trajectory data for all tasks
        if task_graph and task_graph.get('nodes'):
            # Multi-task scenario: read trajectory data for all nodes in task graph
            for node in task_graph['nodes']:
                tid = node.get('id')
                if tid is None:
                    continue
                trajectory_items = await context.get_task_trajectory(tid)
                if not trajectory_items:
                    continue

                # Ensure list format
                if not isinstance(trajectory_items, list):
                    trajectory_items = [trajectory_items]

                # Extract agents and tools from trajectory
                for item in trajectory_items:
                    await MetaKnowledge.extract_agents_and_tools_from_item(context, item, agents_config)
        else:
            # Single task scenario: only read current task's trajectory data
            trajectory_items = await context.get_task_trajectory(task_id)
            if not trajectory_items:
                trajectory_items = []

            # Ensure list format
            if not isinstance(trajectory_items, list):
                trajectory_items = [trajectory_items]

            # Extract agents and tools from trajectory
            for item in trajectory_items:
                await MetaKnowledge.extract_agents_and_tools_from_item(context, item, agents_config)

        # Build meta data
        meta_data = {
            "task_id": task_id,
            "agents": agents_config,
            "agent_count": len(agents_config.keys()),
        }
        return meta_data

    @staticmethod
    async def save_meta(context: Context, swarm_source: str, agents_source: dict[str, str]):
        """
        Save trajectory metadata
        
        Args:
            context: Context object
            swarm_source: Swarm source code
            agents_source: Dictionary of agent source code
        """
        meta_data = {
            "task_id": context.task_id,
            "swarm": swarm_source,
            "agents": agents_source,
            "agent_count": len(agents_source.keys()),
        }
        await save_context_artifact(context, TrajType.META_DATA, meta_data)

    @staticmethod
    async def get_saved_meta(context: Context, task_id: str = None) -> Optional[dict]:
        """
        Get saved trajectory metadata
        
        Args:
            context: Context object
            task_id: Task ID, if None uses context.task_id
            
        Returns:
            Saved metadata dictionary, or None if not found
        """
        data = await get_context_artifact_data(context, TrajType.META_DATA, task_id)
        if not data:
            return None

        if isinstance(data, str):
            try:
                return json.loads(data)
            except Exception as e:
                logger.warning(f"Failed to load saved exp data: {e}")
                return None
        return data
