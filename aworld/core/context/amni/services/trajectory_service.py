# coding: utf-8
"""
Trajectory Service Module

This module provides trajectory data processing, parsing, and visualization functionality, mainly used for:
1. Reading and understanding trajectory data
2. Building and parsing task relationship graphs
3. Converting trajectory data to graph structures
4. Deduplication and transformation of trajectory data
5. Extraction and saving of trajectory metadata
"""

import json
import os
from typing import List, Dict, Optional, Any

from pydantic import BaseModel

from aworld.core.agent.base import AgentFactory
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.base import Context
from aworld.dataset.types import TrajectoryItem
from aworld.logs.util import logger
from aworld.output import ArtifactType
from aworld.output.utils import load_workspace as load_workspace_util
from aworld.core.context.amni.config import get_env_mode


class AgentSnapshot(BaseModel):
    """Agent snapshot data model"""
    id: Optional[str] = None
    name: Optional[str] = None
    prompt: Optional[str] = None
    definition: Optional[str] = None
    diffs: Optional[str] = None


class TrajectoryService:
    """Trajectory service class, providing trajectory data reading, processing, and conversion functionality"""

    @staticmethod
    def get_metadata_dict(item: TrajectoryItem) -> dict:
        """
        Helper function to get metadata
        
        Args:
            item: TrajectoryItem object
            
        Returns:
            metadata dictionary
        """
        meta = item.meta
        if hasattr(meta, 'to_dict'):
            return meta.to_dict()
        elif hasattr(meta, 'model_dump'):
            return meta.model_dump()
        return {}

    @staticmethod
    def get_action_dict(item: TrajectoryItem) -> dict:
        """
        Helper function to get action
        
        Args:
            item: TrajectoryItem object
            
        Returns:
            action dictionary
        """
        action = item.action
        if hasattr(action, 'to_dict'):
            return action.to_dict()
        elif hasattr(action, 'model_dump'):
            return action.model_dump()
        return {}

    @staticmethod
    def get_task_traj_messages(task_traj) -> list:
        """
        Get message list from the last trajectory item
        
        Extract message list from the last TrajectoryItem in trajectory data.
        Supports both Pydantic BaseModel format and dictionary format (backward compatible).
        
        Args:
            task_traj: Trajectory item list, type is List[TrajectoryItem]
            
        Returns:
            Message list extracted from the last trajectory item's state.messages
            Returns empty list [] if trajectory data is empty
        """
        if not task_traj:
            return []
        last_item = task_traj[-1]
        # TrajectoryItem is a Pydantic BaseModel, access via attribute
        if hasattr(last_item, 'state') and hasattr(last_item.state, 'messages'):
            return last_item.state.messages
        # Fallback for dict format (backward compatibility)
        elif isinstance(last_item, dict):
            return last_item.get('state', {}).get('messages', [])
        return []

    @staticmethod
    def get_task_traj_metadata(task_traj) -> dict:
        """
        Get metadata from the last trajectory item
        
        Extract metadata information from the last TrajectoryItem in trajectory data.
        Supports multiple metadata formats: Pydantic model's to_dict(), model_dump() methods, and dictionary format.
        
        Args:
            task_traj: Trajectory item list, type is List[TrajectoryItem]
            
        Returns:
            Metadata dictionary extracted from the last trajectory item's meta
            Returns empty dictionary {} if trajectory data is empty or metadata cannot be extracted
        """
        if not task_traj:
            return {}
        last_item = task_traj[-1]
        # TrajectoryItem is a Pydantic BaseModel, access via attribute
        if hasattr(last_item, 'meta'):
            meta = last_item.meta
            # ExpMeta has to_dict() method
            if hasattr(meta, 'to_dict'):
                return meta.to_dict()
            # Fallback for Pydantic v2
            elif hasattr(meta, 'model_dump'):
                return meta.model_dump()
        # Fallback for dict format (backward compatibility)
        elif isinstance(last_item, dict):
            meta = last_item.get('meta', {})
            if isinstance(meta, dict):
                return meta
            elif hasattr(meta, 'to_dict'):
                return meta.to_dict()
            elif hasattr(meta, 'model_dump'):
                return meta.model_dump()
        return {}

    @staticmethod
    def get_uniq_agentid_task_trajs(traj_data: List[TrajectoryItem]) -> Dict[str, List[TrajectoryItem]]:
        """
        Group TrajectoryItems by task_id and deduplicate same agent_id within each task_id
        
        Args:
            traj_data: Trajectory data list
            
        Returns:
            Dictionary grouped by task_id, each task_id corresponds to a deduplicated TrajectoryItem list
        """
        # Group TrajectoryItems by task_id
        task_items_map = {}
        for item in traj_data:
            task_id = item.meta.task_id
            if task_id not in task_items_map:
                task_items_map[task_id] = []
            task_items_map[task_id].append(item)

        # Keep only the last item for each agent_id (deduplication logic)
        for task_id, items in task_items_map.items():
            tmp_items = []
            for item in items:
                metadata = TrajectoryService.get_metadata_dict(item)
                agent_id = metadata.get('agent_id', None)

                # If no agent_id, add directly
                if agent_id is None:
                    tmp_items.append(item)
                else:
                    # Check if tmp_items contains an item with the same agent_id
                    found = False
                    for i, existing_item in enumerate(tmp_items):
                        existing_metadata = TrajectoryService.get_metadata_dict(existing_item)
                        existing_agent_id = existing_metadata.get('agent_id', None)
                        if existing_agent_id == agent_id:
                            # Overwrite: replace item with same agent_id
                            tmp_items[i] = item
                            found = True
                            break

                    # If no item with same agent_id found, add it
                    if not found:
                        tmp_items.append(item)

            # Update items in task_items_map
            task_items_map[task_id] = tmp_items

        return task_items_map

    @staticmethod
    def convert_to_store_data(traj_data: List[TrajectoryItem]) -> List[dict]:
        """
        Convert trajectory data to storage format
        
        Args:
            traj_data: Trajectory data list
            
        Returns:
            Converted dictionary list
        """
        task_items_map = TrajectoryService.get_uniq_agentid_task_trajs(traj_data)

        # Flatten all items in task_items_map into an array
        flattened_items = []
        for task_id, items in task_items_map.items():
            flattened_items.extend(items)

        return [item.to_dict() for item in flattened_items]

    @staticmethod
    def _read_task_relation_data(context: Context) -> Optional[List[Dict]]:
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
    def remove_target_agent_traj_and_parents(context: Context, traj_data: List[TrajectoryItem], target_agent_id: str) -> List[TrajectoryItem]:
        """
        Remove trajectory of specified agent and its parent tasks
        
        Find all related tasks based on target_agent_id, then remove trajectory data for these tasks and their parent tasks.
        Used to filter out all trajectory information generated by a specific agent.
        
        Args:
            context: Context object for getting task relation data
            traj_data: Trajectory data list containing TrajectoryItems for all tasks
            target_agent_id: ID of the agent to remove
            
        Returns:
            Filtered trajectory data list with all trajectories of specified agent and its parent tasks removed
            Returns empty list [] if traj_data is empty
        """
        if not traj_data:
            return []

        to_delete_parents_task_ids = []
        # Iterate through all TrajectoryItems to find task_ids containing target_agent_id
        for item in traj_data:
            metadata = TrajectoryService.get_metadata_dict(item)
            if metadata.get('agent_id', None) == target_agent_id:
                task_id = item.meta.task_id
                if task_id not in to_delete_parents_task_ids:
                    to_delete_parents_task_ids.append(task_id)

        edges = TrajectoryService._read_task_relation_data(context)
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

    @staticmethod
    def _extract_tool_call_input(tool_call_id: str, tool_calls: List[Any]) -> str:
        """
        Find matching tool_call from previous assistant's tool_calls and extract its input content
        
        Args:
            tool_call_id: ID of the tool_call to find
            tool_calls: List of tool_calls in assistant message
            
        Returns:
            function.arguments of the matching tool_call, returns empty string if not found or no arguments
        """
        if not tool_call_id or tool_call_id == 'tool' or not tool_calls:
            return ''

        for tc in tool_calls:
            # tool_call may be in dictionary format, need to check id field
            tc_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
            if tc_id == tool_call_id:
                # Take tool_call's function.arguments as input, if not available take the entire tool_call's string representation
                if isinstance(tc, dict):
                    function = tc.get('function', {})
                    if function:
                        return function.get('arguments', '') or str(tc)
                    else:
                        return str(tc)
                else:
                    # If it's an object, try to get function.arguments
                    if hasattr(tc, 'function') and hasattr(tc.function, 'arguments'):
                        return tc.function.arguments or str(tc)
                    else:
                        return str(tc)

        return ''

    @staticmethod
    def parse_traj_to_graph(context: Context, traj_data: List[TrajectoryItem]) -> Dict[str, List]:
        """
        Parse trajectory data into graph structure (nodes and edges) (entry interface)
        
        Convert TrajectoryItem list to visualizable graph structure. This is the core conversion function
        for trajectory data visualization. The function converts raw trajectory data into a graph structure
        containing task nodes, message nodes, and edges for subsequent HTML visualization.
        
        Args:
            context: Context object for getting task relations and session_id
                    - context.get_task_graph(): Get task graph containing edges information
                    - context.session_id: Session ID for cleaning display labels
            traj_data: Trajectory data list containing TrajectoryItems for all tasks
                      - Each TrajectoryItem contains meta, state, action fields
                      - meta.task_id identifies which task the trajectory belongs to
                      - state.messages contains message list
                      - Message types include: system, user, assistant, tool
            
        Returns:
            Dictionary containing nodes and edges:
            {
                "nodes": [
                    # Task node
                    {
                        "id": str,              # Task ID
                        "label": str,           # Display label
                        "type": "task",         # Node type
                        "task_index": int,      # Task index
                        "message_count": int    # Message count
                    },
                    # Message node (assistant or tool)
                    {
                        "id": str,              # Unique ID
                        "label": str,           # Display label
                        "input": str,           # Input content
                        "output": str,          # Output content
                        "tool_call": str,       # tool_calls (only for assistant nodes)
                        "type": str,            # Node type: assistant or tool
                        "task_id": str,         # Belonging task ID
                        "task_index": int,      # Task index
                        "msg_index": int,       # Message index
                        "is_sub_task": bool,    # Whether associated with subtask
                        "is_agent_finished": bool  # Whether agent is finished
                    },
                    ...
                ],
                "edges": [
                    {
                        "source": str,          # Source task ID
                        "target": str,          # Target task ID
                        "metadata": dict        # Edge metadata
                    },
                    ...
                ]
            }
            Returns {"nodes": [], "edges": []} if traj_data is empty
        """
        nodes = []
        edges = []

        if not traj_data:
            return {"nodes": nodes, "edges": edges}

        id_counter = {}  # Track occurrence count of each original ID
        task_index = 0  # Task index for calculating position

        edges = TrajectoryService._read_task_relation_data(context) or []

        # Sort traj_data according to edges connection order
        traj_data = TrajectoryService.sort_traj_data_by_edges(traj_data, edges)

        # Deduplicate
        task_items_map = TrajectoryService.get_uniq_agentid_task_trajs(traj_data)

        # Iterate through each task
        for task_id, items in task_items_map.items():
            if not items:
                continue

            msgs = []
            for item in items:
                metadata = TrajectoryService.get_metadata_dict(item)
                action = TrajectoryService.get_action_dict(item)
                for message in item.state.messages:
                    msgs.append({'metadata': metadata, 'message': message, 'action': action})

            message_count = len(msgs) if msgs else 0

            # Create task container node (large box)
            current_task_index = task_index  # Save current task_index for subsequent message nodes
            # Use first agent's name as label, if not available use task_id
            # Label takes agent_name, if no agent_name takes agent_id, finally remove trailing session_id if exists
            agent_name = TrajectoryService.get_metadata_dict(items[0]).get('agent_id', task_id)
            if hasattr(context, 'session_id') and agent_name.endswith(f"_{context.session_id}"):
                agent_name = agent_name[:-len(f"_{context.session_id}")]

            task_node = {
                "id": task_id,
                "label": agent_name,
                "type": "task",
                "task_index": current_task_index,  # For calculating position in HTML
                "message_count": message_count  # For calculating width in HTML
            }
            nodes.append(task_node)
            task_index += 1

            # First find user message content (for first assistant's input)
            user_message_content = ''
            for msg in msgs:
                message = msg.get('message', {})
                if message.get('role') == 'user':
                    user_message_content = message.get('content', '')
                    break

            assistant_count = 0
            last_tool_call_content = ''  # Record previous tool_call's content
            last_assistant_tool_calls = []  # Record previous assistant's tool_calls list
            # Iterate through each message, create child nodes, positioned within task
            for msg_idx, msg in enumerate(msgs):
                # Process Message object
                metadata = msg.get('metadata', {})
                action = msg.get('action', {})
                message = msg.get('message', {})

                # Filter out system and user type messages (not displayed in graph)
                role = message.get('role', None)
                if role == 'system' or role == 'user':
                    continue

                tool_calls = None
                # Process assistant type messages
                if role == 'assistant':
                    original_id = metadata.get('agent_id', 'assistant')
                    # If this assistant is the first, input takes user message content, otherwise takes previous tool_call content
                    if assistant_count == 0:
                        input_content = user_message_content
                    else:
                        input_content = last_tool_call_content
                    output = message.get('content', '')
                    tool_calls_list = message.get('tool_calls', [])
                    tool_calls = str(tool_calls_list)
                    # Save tool_calls list for subsequent tool message lookup
                    last_assistant_tool_calls = tool_calls_list if isinstance(tool_calls_list, list) else []
                    assistant_count += 1  # For subsequent subtask association judgment
                # Process tool type messages
                elif role == 'tool':
                    tool_call_id = message.get('tool_call_id', 'tool')
                    original_id = tool_call_id
                    # Find matching tool_call from previous assistant's tool_calls
                    input_content = TrajectoryService._extract_tool_call_input(tool_call_id, last_assistant_tool_calls)
                    output = message.get('content', '')
                    # Record tool_call content for next assistant to use
                    last_tool_call_content = output
                    # Ignore agent calls (agent call) to avoid displaying in graph
                    if AgentFactory.agent_instance(original_id) is not None:
                        continue
                else:
                    # Skip other message types
                    continue

                if not original_id:
                    continue

                # Determine if associated with subtask
                # If it's a tool message and has associated subtask at current step, mark as subtask node
                is_sub_task = False
                if role == 'tool' and TrajectoryService.find_current_step_sub_task(edges, task_id, assistant_count) != []:
                    is_sub_task = True
                    logger.info(f'find related sub task, skip {original_id}')

                # Calculate ID occurrence count, generate unique ID
                if original_id in id_counter:
                    id_counter[original_id] += 1
                    unique_id = f"{original_id}_{id_counter[original_id]}"
                else:
                    id_counter[original_id] = 0
                    unique_id = original_id

                # Also remove session_id from original_id processing
                display_label = original_id
                if hasattr(context, 'session_id') and context.session_id and display_label and isinstance(display_label, str) and display_label.endswith(f"_{context.session_id}"):
                    display_label = display_label[:-len(f"_{context.session_id}")]

                # Create message node
                message_node = {
                    "id": f"{task_id}_{unique_id}_{msg_idx}",
                    "label": display_label,
                    "input": input_content,
                    "output": output,
                    "tool_call": tool_calls,
                    "type": role,
                    "task_id": task_id,  # For calculating position in HTML
                    "task_index": current_task_index,  # Use corresponding task_index
                    "msg_index": msg_idx,  # For calculating position in HTML
                    "is_sub_task": is_sub_task,
                    "is_agent_finished": action.get('is_agent_finished', True)
                }
                nodes.append(message_node)

                # Merge consecutive multiple tool nodes into one node (optimize display)
                # This reduces the number of nodes in the graph, making visualization clearer
                merged_nodes = []
                for idx, node in enumerate(nodes):
                    # If current node is tool type and previous node is also tool type, merge
                    if node.get('type') == 'tool' and merged_nodes and merged_nodes[-1].get('type') == 'tool':
                        # Merge label, input and output to last node
                        merged_nodes[-1]['label'] = merged_nodes[-1].get('label', '') + '\n' + node.get('label', '')
                        merged_nodes[-1]['input'] = merged_nodes[-1].get('input', '') + '\n' + node.get('input', '')
                        merged_nodes[-1]['output'] = merged_nodes[-1].get('output', '') + '\n' + node.get('output', '')
                    else:
                        # Not tool type or not consecutive tool nodes, add directly
                        merged_nodes.append(node)
                nodes = merged_nodes

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    async def _extract_agents_and_tools_from_item(context: ApplicationContext, item: TrajectoryItem, agents_config: dict):
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
        definition = await context.agent_registry_service.load_as_source(name=agent.name(),
                                                                          session_id=context.session_id)
        diffs = await context.agent_registry_service.compare_versions(name=agent.name(),
                                                                      session_id=context.session_id,
                                                                      format="context")
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
                    await TrajectoryService._extract_agents_and_tools_from_item(context, item, agents_config)
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
                await TrajectoryService._extract_agents_and_tools_from_item(context, item, agents_config)

        # Build meta data
        meta_data = {
            "task_id": task_id,
            "agents": agents_config,
            "agent_count": len(agents_config.keys()),
        }
        return meta_data

    @staticmethod
    async def get_running_exp(context: Context) -> Optional[List[TrajectoryItem]]:
        """
        Read trajectory data (entry interface)

        Read trajectory data from context, supporting both single-task and multi-task scenarios.
        This is the entry function for trajectory data processing flow, responsible for extracting
        all related trajectory data from context.

        Args:
            context: Context object containing task graph and trajectory data
                    - context.get_task_graph(): Get task graph, returns dictionary containing nodes and edges
                    - context.get_task_trajectory(task_id): Get trajectory data for specified task
                    - context.task_id: Current task ID

        Returns:
            Trajectory data list containing TrajectoryItem objects for all tasks:
            - Each TrajectoryItem contains complete trajectory information (meta, state, action, etc.)
            - task_id is stored in TrajectoryItem.meta.task_id
            - Returns None if no trajectory data is found
        """
        task_graph = context.get_task_graph()
        # Single task scenario: only read current task's trajectory data
        if not task_graph or not task_graph.get('nodes'):
            task_traj = await context.get_task_trajectory(context.task_id)
            if not task_traj:
                return None
            # Ensure list format is returned
            return list(task_traj) if isinstance(task_traj, list) else [task_traj]

        # Multi-task scenario: read trajectory data for all nodes in task graph
        all_trajectory_items = []
        for node in task_graph['nodes']:
            tid = node.get('id')
            if tid is None:
                continue
            task_traj = await context.get_task_trajectory(tid)
            if task_traj:
                # Handle single trajectory item or trajectory item list
                if isinstance(task_traj, list):
                    all_trajectory_items.extend(task_traj)
                else:
                    all_trajectory_items.append(task_traj)

        return all_trajectory_items if all_trajectory_items else None

    @staticmethod
    async def save_meta(context: Context, swarm_source: str, agents_source: dict[str, str]):
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

    @staticmethod
    async def get_saved_exp(context: Context, task_id: str = None) -> Optional[List[TrajectoryItem]]:
        """
        Get saved trajectory data
        """
        data = await get_context_artifact_data(context, TrajType.EXP_DATA, task_id)
        if not data:
            return None

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception as e:
                logger.warning(f"Failed to load saved exp data: {e}")
                return None

        if isinstance(data, list):
            return [TrajectoryItem(**item) for item in data if isinstance(item, dict)]

        return None


# ============================================================================
# Trajectory types and utility functions (from base.py)
# ============================================================================

class TrajType:
    """Trajectory data type constants"""
    EXP_DATA = 'mind_stream_exp_data'
    GRAPH_DATA = 'mind_stream_graph_data'
    META_DATA = 'mind_stream_meta_data'
    MULTI_TURN_TASK_ID_DATA = 'multi_task_id_data'
    META_LEARNING_REPORT_DATA = 'meta_learning_report_data'


class MindStreamType:
    """Mind Stream type constants"""
    MIND_STREAM_HTML = 'mind_stream_html'
    MIND_STREAM_REMOVED_HTML_URL = 'mind_stream_removed_html_url'
    MIND_STREAM_REMOVED_HTML = 'mind_stream_removed_html'


def _convert_to_json_serializable(obj: Any) -> Any:
    """
    Recursively convert object to JSON serializable format
    Handles Pydantic models, dictionaries, lists, etc.
    
    Args:
        obj: Object to convert
        
    Returns:
        JSON serializable object
    """
    # If it's a Pydantic model, convert to dictionary (compatible with v1 and v2)
    if isinstance(obj, BaseModel):
        if hasattr(obj, 'model_dump'):
            return obj.model_dump()  # Pydantic v2
        elif hasattr(obj, 'dict'):
            return obj.dict()  # Pydantic v1
        else:
            return dict(obj)
    
    # If object has model_dump method (may be other types of Pydantic-compatible objects)
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    elif hasattr(obj, 'dict'):
        return obj.dict()
    
    # If it's a dictionary, recursively process values
    if isinstance(obj, dict):
        return {key: _convert_to_json_serializable(value) for key, value in obj.items()}
    
    # If it's a list or tuple, recursively process elements
    if isinstance(obj, (list, tuple)):
        return [_convert_to_json_serializable(item) for item in obj]
    
    # Other types return directly (strings, numbers, booleans, None, etc.)
    return obj


async def load_workspace(context):
    """
    Load workspace
    
    Args:
        context: Context object
        
    Returns:
        workspace object
    """
    session_id = context.session_id
    if hasattr(context, 'workspace'):
        workspace = context.workspace
    else:
        workspace_type = os.environ.get("WORKSPACE_TYPE", "local")
        workspace_path = os.environ.get("WORKSPACE_PATH", "./data/workspaces")
        workspace = await load_workspace_util(session_id, workspace_type, workspace_path)
    return workspace


async def get_artifact_data(context, artifact_id) -> Dict:
    """
    Get artifact data
    
    Args:
        context: Context object
        artifact_id: artifact ID
        
    Returns:
        artifact data
    """
    session_id = context.session_id
    if os.getenv('MIND_STREAM_DEBUG_MODE', 'false').lower() == 'true' and get_env_mode() == 'dev':
        # Automatically create directory
        file_path = f'{os.environ.get("TRAJ_STORAGE_BASE_PATH", "./")}/{session_id}/{artifact_id}'
        try:
            with open(file_path, 'r') as f:
                return f.read()
        except FileNotFoundError:
            return None

    workspace = await load_workspace(context)
    data = workspace.get_artifact_data(artifact_id)
    return data


async def get_context_artifact_data(context, context_key, task_id) -> Dict:
    """
    Get artifact data from context
    
    Args:
        context: Context object
        context_key: key in context
        
    Returns:
        artifact data
    """
    artifact_id = build_artifact_id(context_key, task_id)
    return await get_artifact_data(context, artifact_id)

async def save_artifact(context, artifact_id, data):
    """
    Save artifact
    
    Args:
        context: Context object
        artifact_id: artifact ID
        data: Data to save
        is_retain_id: Whether to retain ID
        
    Returns:
        artifact_id
    """
    session_id = context.session_id
    if os.getenv('MIND_STREAM_DEBUG_MODE', 'false').lower() == 'true' and get_env_mode() == 'dev':
        # Automatically create directory
        file_path = f'{os.environ.get("TRAJ_STORAGE_BASE_PATH", "./")}/{session_id}/{artifact_id}'
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            # Ensure writing JSON string
            if isinstance(data, str):
                # If already a string, try to validate if it's valid JSON
                try:
                    json.loads(data)  # Validate if it's valid JSON
                    json_str = data
                except (json.JSONDecodeError, ValueError):
                    # Not a valid JSON string, write directly
                    json_str = data
            elif isinstance(data, (dict, list)):
                # Dictionary or list, first convert to JSON serializable format, then to JSON string
                serializable_data = _convert_to_json_serializable(data)
                json_str = json.dumps(serializable_data, ensure_ascii=False)
            else:
                # Other types, first try to convert to JSON serializable format
                try:
                    serializable_data = _convert_to_json_serializable(data)
                    json_str = json.dumps(serializable_data, ensure_ascii=False)
                except (TypeError, ValueError):
                    # If cannot serialize, convert to string
                    json_str = str(data)
            f.write(json_str)
            return artifact_id

    workspace = await load_workspace(context)

    # delete existed old html artifact
    await workspace.delete_artifact(artifact_id)

    # Ensure content is JSON string
    if isinstance(data, str):
        # If already a string, try to validate if it's valid JSON
        try:
            json.loads(data)  # Validate if it's valid JSON
            content = data
        except (json.JSONDecodeError, ValueError):
            # Not a valid JSON string, use directly
            content = data
    elif isinstance(data, (dict, list)):
        # Dictionary or list, first convert to JSON serializable format, then to JSON string
        serializable_data = _convert_to_json_serializable(data)
        content = json.dumps(serializable_data, ensure_ascii=False)
    else:
        # Other types, first try to convert to JSON serializable format
        try:
            serializable_data = _convert_to_json_serializable(data)
            content = json.dumps(serializable_data, ensure_ascii=False)
        except (TypeError, ValueError):
            # If cannot serialize, convert to string
            content = str(data)

    await workspace.create_artifact(
        artifact_type=ArtifactType.HTML,
        artifact_id=artifact_id,
        content=content,
        metadata={
            "session_id": session_id
        }
    )
    return artifact_id


def build_artifact_id(context_key, task_id):
    """
    Build artifact ID
    
    Args:
        context_key: context key
        task_id: Task ID
        
    Returns:
        artifact ID
    """
    return f"{context_key}_{task_id}"


async def save_context_artifact(context, context_key, data):
    """
    Save context artifact
    
    Args:
        context: Context object
        context_key: context key
        data: Data to save
        
    Returns:
        artifact_id
    """
    artifact_id = build_artifact_id(context_key, context.task_id)

    await save_artifact(context, artifact_id, data)

    context.put(context_key, artifact_id)
    return artifact_id


async def append_traj_id_to_session_artifact(context, task_id) -> str:
    """
    Append id to session file
    
    Args:
        context: Context object
        task_id: Task ID
        
    Returns:
        Updated content
    """
    content = await get_artifact_data(context=context, artifact_id=TrajType.MULTI_TURN_TASK_ID_DATA)
    logger.info(f'append_traj_id_to_session_artifact|save_task_ids|{content} {task_id}')

    if content is None:
        content = task_id
    else:
        # Check if task_id already exists
        existing_ids = [line.strip() for line in content.strip().split('\n') if line.strip()]
        if task_id not in existing_ids:
            content = f'{content}\n{task_id}'

    await save_artifact(context=context, artifact_id=TrajType.MULTI_TURN_TASK_ID_DATA, data=content)
    return content

