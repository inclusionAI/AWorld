# coding: utf-8
"""
DAG Knowledge Module

This module provides DAG (Directed Acyclic Graph) processing functionality, mainly used for:
1. Reading and processing task relation data (graph edges)
2. Finding subtasks and task relationships
3. Topologically sorting trajectory data based on task dependencies
4. Filtering trajectory data based on agent and task relationships
"""

from typing import List, Dict, Optional

from aworld.core.context.base import Context
from aworld.dataset.types import TrajectoryItem


class DagKnowledge:
    """DAG knowledge class, providing task graph processing functionality"""

    @staticmethod
    def read_task_relation_data(context: Context) -> Optional[List[Dict]]:
        """
        Read task relation data (graph edge data)
        
        Get all edges from the task graph in context, representing dependencies between tasks.
        Cleans agent information from edge data, keeping only task relations.
        
        Args:
            context: Context object containing task graph information
            
        Returns:
            List of edges, each edge contains source, target and metadata
            May return None if task graph does not exist
        """
        task_graph = context.get_task_graph()
        if not task_graph:
            return None
        edges = task_graph.get('edges', [])
        # Clean agent information from edge data to avoid displaying in visualization
        for edge in edges:
            if 'metadata' in edge and 'agents' in edge['metadata']:
                del edge['metadata']['agents']
        return edges

    @staticmethod
    def find_sub_task(edges: List[Dict], source: str) -> List[Dict]:
        """
        Find all subtasks of the specified source task
        
        Find all task relationships with the specified task as source based on the edge's source field.
        
        Args:
            edges: List of edges, each edge contains source and target
            source: Source task ID
            
        Returns:
            List of matching edges, all edges where source equals the specified source
        """
        result = []
        for edge in edges:
            if source == edge.get('source', None):
                result.append(edge)
        return result

    @staticmethod
    def find_current_step_sub_task(edges: List[Dict], source: str, step: int) -> List[Dict]:
        """
        Find subtasks called by the specified source task at the specified step
        
        Find subtask relationships called at a specific step based on the edge's source
        and caller_info.agent_step field in metadata.
        
        Args:
            edges: List of edges, each edge contains source, target and metadata
            source: Source task ID
            step: Agent step number
            
        Returns:
            List of matching edges that satisfy both source and agent_step conditions
        """
        result = []
        for edge in edges:
            if source == edge.get('source', None) \
                    and edge.get('metadata', None) is not None \
                    and edge.get('metadata', {}).get('caller_info', None) is not None \
                    and edge.get('metadata', {}).get('caller_info', {}).get('agent_step', None) == step:
                result.append(edge)
        return result

    @staticmethod
    def sort_traj_data_by_edges(traj_data: List[TrajectoryItem], edges: List[Dict]) -> List[TrajectoryItem]:
        """
        Topologically sort trajectory data according to edge connection order
        
        Use topological sort algorithm to sort trajectory data based on dependencies between tasks (edges).
        Ensures that dependent tasks are placed before tasks that depend on them.
        
        Args:
            traj_data: Trajectory data list containing TrajectoryItems for all tasks
            edges: List of edges, each edge contains source (source task) and target (target task)
                   source -> target means source depends on target
            
        Returns:
            Sorted trajectory data list, arranged in topological order by task_id
            Returns original traj_data directly if edges or traj_data is empty
        """
        if not edges or not traj_data:
            return traj_data

        # Group TrajectoryItems by task_id
        task_items_map = {}
        for item in traj_data:
            task_id = item.meta.task_id
            if task_id not in task_items_map:
                task_items_map[task_id] = []
            task_items_map[task_id].append(item)

        # Build dependency graph: source -> [targets]
        graph = {}
        in_degree = {}

        # Initialize in-degree for all nodes
        for task_id in task_items_map.keys():
            in_degree[task_id] = 0
            graph[task_id] = []

        # Build graph and calculate in-degree
        for edge in edges:
            source = edge.get('source')
            target = edge.get('target')
            if source and target and source in task_items_map and target in task_items_map:
                if target not in graph:
                    graph[target] = []
                graph[source].append(target)
                in_degree[target] = in_degree.get(target, 0) + 1

        # Topological sort: find all nodes with in-degree 0
        queue = [task_id for task_id in task_items_map.keys() if in_degree.get(task_id, 0) == 0]
        sorted_order = []

        while queue:
            current = queue.pop(0)
            sorted_order.append(current)

            # Decrease in-degree of all neighbors
            for neighbor in graph.get(current, []):
                in_degree[neighbor] = in_degree.get(neighbor, 0) - 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Add nodes not in sorted order (may be independent nodes)
        for task_id in task_items_map.keys():
            if task_id not in sorted_order:
                sorted_order.append(task_id)

        # Rebuild list according to sorted order
        sorted_traj_data = []
        for task_id in sorted_order:
            if task_id in task_items_map:
                sorted_traj_data.extend(task_items_map[task_id])

        return sorted_traj_data

    @staticmethod
    def remove_target_agent_traj_and_parents(context: Context, traj_data: List[TrajectoryItem], target_agent_id: str, get_metadata_dict_func) -> List[TrajectoryItem]:
        """
        Remove trajectory of specified agent and its parent tasks
        
        Find all related tasks based on target_agent_id, then remove trajectory data for these tasks and their parent tasks.
        Used to filter out all trajectory information generated by a specific agent.
        
        Args:
            context: Context object for getting task relation data
            traj_data: Trajectory data list containing TrajectoryItems for all tasks
            target_agent_id: ID of the agent to remove
            get_metadata_dict_func: Function to get metadata dictionary from TrajectoryItem
            
        Returns:
            Filtered trajectory data list with all trajectories of specified agent and its parent tasks removed
            Returns empty list [] if traj_data is empty
        """
        if not traj_data:
            return []

        to_delete_parents_task_ids = []
        # Iterate through all TrajectoryItems to find task_ids containing target_agent_id
        for item in traj_data:
            metadata = get_metadata_dict_func(item)
            if metadata.get('agent_id', None) == target_agent_id:
                task_id = item.meta.task_id
                if task_id not in to_delete_parents_task_ids:
                    to_delete_parents_task_ids.append(task_id)

        edges = DagKnowledge.read_task_relation_data(context)
        if edges:
            for edge in edges:
                if edge.get('target', None) in to_delete_parents_task_ids:
                    # Add to deletion list
                    source_task_id = edge.get('source', None)
                    if source_task_id and source_task_id not in to_delete_parents_task_ids:
                        to_delete_parents_task_ids.append(source_task_id)

        # Filter out TrajectoryItems corresponding to task_ids that need to be deleted
        filtered_traj_data = [
            item for item in traj_data
            if item.meta.task_id not in to_delete_parents_task_ids
        ]

        return filtered_traj_data
