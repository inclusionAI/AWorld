#!/usr/bin/env python3
"""
Flame graph analysis tool
Draw flame graphs based on node execute_time and end_time
Supports interactive display and proper handling of time overlaps
"""
import logging
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from aworld.runners.state_manager import RunNode


def _check_overlap(interval1: Tuple[float, float], interval2: Tuple[float, float]) -> bool:
    """Check if two time intervals overlap"""
    start1, end1 = interval1
    start2, end2 = interval2
    return not (end1 <= start2 or end2 <= start1)


def _assign_y_positions(nodes: List[Dict], min_time: float) -> Dict[str, int]:
    """
    Assign y positions to nodes, handling time overlap cases
    First fix AGENT and TOOL type nodes at the bottom layer (y=0), then process other nodes with stacking logic
    Only stack when time intervals truly overlap (have intersection), nodes with identical times are placed on the same layer
    """
    # Separate AGENT/TOOL nodes and other nodes
    agent_tool_nodes = []
    other_nodes = []
    
    for node_info in nodes:
        busi_type = node_info['node'].busi_type
        if busi_type in ['AGENT', 'TOOL']:
            agent_tool_nodes.append(node_info)
        else:
            other_nodes.append(node_info)
    
    # Sort AGENT/TOOL nodes by duration from large to small
    agent_tool_nodes.sort(key=lambda n: (
        -(n['end_time'] - n['start_time']),  # Duration from large to small
        n['start_time']  # When duration is the same, sort by start time
    ))
    
    # Sort other nodes by duration from large to small
    other_nodes.sort(key=lambda n: (
        -(n['end_time'] - n['start_time']),  # Duration from large to small
        n['start_time']  # When duration is the same, sort by start time
    ))
    
    # Assign y position to each node
    y_positions = {}
    # Each element is a list containing (node_id, start_time, end_time, duration) tuples of all nodes on that layer
    occupied_layers = []
    
    # Step 1: Process AGENT and TOOL nodes, fix at bottom layer (y=0)
    for node_info in agent_tool_nodes:
        node_id = node_info['node'].node_id
        start_time = node_info['start_time']
        end_time = node_info['end_time']
        duration = end_time - start_time
        
        # Place directly at bottom layer (y=0)
        y_positions[node_id] = 0
        # If bottom layer is not initialized yet, initialize it
        if len(occupied_layers) == 0:
            occupied_layers.append([])
        occupied_layers[0].append((node_id, start_time, end_time, duration))
    
    # Step 2: Process other nodes, stack on top of existing layers
    for node_info in other_nodes:
        node_id = node_info['node'].node_id
        start_time = node_info['start_time']
        end_time = node_info['end_time']
        duration = end_time - start_time
        current_interval = (start_time, end_time)
        
        # Start from bottom layer to find a layer where it can be placed
        layer_idx = None
        for idx, layer_nodes in enumerate(occupied_layers):
            # Check if there are nodes on this layer that overlap with current node
            has_overlap = False
            for other_node_id, other_start, other_end, other_duration in layer_nodes:
                other_interval = (other_start, other_end)
                # If time is exactly the same, can be placed on the same layer
                if start_time == other_start and end_time == other_end:
                    continue
                # Check if there is intersection (true overlap)
                if _check_overlap(current_interval, other_interval):
                    has_overlap = True
                    break
            
            # If there are no overlapping nodes on this layer, can place here
            if not has_overlap:
                layer_idx = idx
                break
        
        # If no available layer found, create new layer
        if layer_idx is None:
            layer_idx = len(occupied_layers)
            occupied_layers.append([])
        
        # Place node on this layer
        y_positions[node_id] = layer_idx
        occupied_layers[layer_idx].append((node_id, start_time, end_time, duration))
    
    return y_positions


def _calculate_statistics(nodes: List[RunNode]) -> Dict:
    """Calculate duration statistics"""
    stats = {
        'total_nodes': len(nodes),
        'by_type': defaultdict(lambda: {'count': 0, 'total_time': 0.0, 'avg_time': 0.0, 'max_time': 0.0, 'min_time': float('inf')}),
        'total_duration': 0.0,
        'max_depth': 0
    }
    
    # Calculate global time range
    valid_nodes = [n for n in nodes if n.execute_time and n.end_time]
    if not valid_nodes:
        return stats
    
    min_time = min(n.execute_time for n in valid_nodes)
    max_time = max(n.end_time for n in valid_nodes)
    stats['total_duration'] = max_time - min_time
    
    # Statistics by type
    for node in valid_nodes:
        duration = node.end_time - node.execute_time
        type_stats = stats['by_type'][node.busi_type]
        type_stats['count'] += 1
        type_stats['total_time'] += duration
        type_stats['max_time'] = max(type_stats['max_time'], duration)
        type_stats['min_time'] = min(type_stats['min_time'], duration)
    
    # Calculate average
    for type_stats in stats['by_type'].values():
        if type_stats['count'] > 0:
            type_stats['avg_time'] = type_stats['total_time'] / type_stats['count']
        if type_stats['min_time'] == float('inf'):
            type_stats['min_time'] = 0.0
    
    # Calculate maximum depth (through parent_node_id relationship)
    node_dict = {node.node_id: node for node in valid_nodes}
    def get_depth(node_id: str, visited: set = None) -> int:
        if visited is None:
            visited = set()
        if node_id in visited or node_id not in node_dict:
            return 0
        visited.add(node_id)
        node = node_dict[node_id]
        if not node.parent_node_id or node.parent_node_id not in node_dict:
            return 1
        return 1 + get_depth(node.parent_node_id, visited)
    
    for node in valid_nodes:
        depth = get_depth(node.node_id)
        stats['max_depth'] = max(stats['max_depth'], depth)
    
    return stats


def plot_flame_graph(nodes: List[RunNode], task_id: str, output_path: Optional[str] = None):
    """
    Draw interactive flame graph based on node execute_time and end_time
    Supports mouse hover to display detailed information, properly handles time overlaps
    
    Args:
        nodes: List of all nodes for the task
        task_id: Task ID
        output_path: Output path (HTML file), if None then display the graph
    """
    if not nodes:
        logging.warning(f"Task {task_id} has no node data, cannot draw flame graph")
        return
    
    # Filter nodes with valid time information
    valid_nodes = []
    for node in nodes:
        if node.execute_time and node.end_time and node.end_time > node.execute_time:
            valid_nodes.append(node)
    
    if not valid_nodes:
        logging.warning(f"Task {task_id} has no valid node time data, cannot draw flame graph")
        return
    
    # Calculate global time range
    min_time = min(node.execute_time for node in valid_nodes)
    max_time = max(node.end_time for node in valid_nodes)
    total_duration = max_time - min_time
    
    if total_duration <= 0:
        logging.warning(f"Task {task_id} has time range of 0, cannot draw flame graph")
        return
    
    # Build node tree structure, calculate tree depth
    node_dict = {node.node_id: node for node in valid_nodes}
    def get_tree_depth(node_id: str, visited: set = None) -> int:
        if visited is None:
            visited = set()
        if node_id in visited or node_id not in node_dict:
            return 0
        visited.add(node_id)
        node = node_dict[node_id]
        if not node.parent_node_id or node.parent_node_id not in node_dict:
            return 1
        return 1 + get_tree_depth(node.parent_node_id, visited)
    
    # Prepare node data
    node_data = []
    for node in valid_nodes:
        tree_depth = get_tree_depth(node.node_id)
        node_data.append({
            'node': node,
            'start_time': node.execute_time,
            'end_time': node.end_time,
            'duration': node.end_time - node.execute_time,
            'tree_depth': tree_depth
        })
    
    # Assign y positions, handle overlaps
    y_positions = _assign_y_positions(node_data, min_time)
    
    # Calculate statistics
    stats = _calculate_statistics(valid_nodes)
    
    # Set colors for different busi_type
    busi_type_colors = {
        'AGENT': '#4ECDC4',
        'TOOL': '#95E1D3',
        'TASK': '#FF6B6B',
        'TOOL_CALLBACK': '#F38181',
        "REMOTE_TOOL_CALL": '#CCCCCC',
        "LLM": '#9B59B6',
        'HUMAN': '#AA96DA',
        'MEMORY': '#FCBAD3',
        'CONTEXT': '#FFD93D',
        'INIT_TOOLS': '#FFA500',
        'INIT_SERVER': '#FF8C00',
        'HANDLER': '#2ECC71'
    }
    
    # Set display labels for different busi_type in HTML page
    busi_type_labels = {
        'AGENT': 'AGENT',
        'TOOL': 'TOOL',
        'TASK': 'TASK',
        'TOOL_CALLBACK': 'TOOL_CALLBACK',
        "REMOTE_TOOL_CALL": 'REMOTE_TOOL_CALL',
        "LLM": 'LLM',
        'HUMAN': 'HUMAN',
        'MEMORY': 'HISTORY',
        'CONTEXT': 'CONTEXT',
        'INIT_TOOLS': 'INIT_TOOLS',
        'INIT_SERVER': 'INIT_SERVER',
        'HANDLER': 'HANDLER'
    }
    
    # Create subplots: main graph and statistics
    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.1,
        subplot_titles=('Execution Flame Graph', 'Duration Statistics'),
        specs=[[{"type": "scatter"}], [{"type": "table"}]]
    )
    
    # Use filled area to draw rectangles, support hover
    annotations = []
    
    # Define drawing order: draw AGENT and TOOL first (base types, should be at bottom layer), then draw other types
    draw_order = ['AGENT', 'TOOL'] + [t for t in busi_type_colors.keys() if t not in ['AGENT', 'TOOL']]
    
    # Draw by type groups, draw AGENT and TOOL first
    for busi_type in draw_order:
        type_nodes = [nd for nd in node_data if nd['node'].busi_type == busi_type]
        if not type_nodes:
            continue
        
        color = busi_type_colors[busi_type]
        
        # Create rectangle for each node (using filled area)
        for node_info in type_nodes:
            node = node_info['node']
            start_time = node_info['start_time']
            end_time = node_info['end_time']
            duration = node_info['duration']
            
            x_start = start_time - min_time  # In seconds
            x_end = end_time - min_time  # In seconds
            width = x_end - x_start
            
            y_pos = y_positions[node.node_id]
            y_bottom = y_pos - 0.4
            y_top = y_pos + 0.4
            
            # Prepare hover information
            display_label = busi_type_labels.get(node.busi_type, node.busi_type)
            hover_text = (
                f"<b>asd {duration:.3f}s {display_label}: {node.busi_id}</b><br>"
            )
            
            # Use filled area to draw rectangle (draw four vertices of rectangle clockwise)
            fig.add_trace(
                go.Scatter(
                    x=[x_start, x_end, x_end, x_start, x_start],
                    y=[y_bottom, y_bottom, y_top, y_top, y_bottom],
                    mode='lines',
                    fill='toself',
                    fillcolor=color,
                    line=dict(color='black', width=1),
                    hovertemplate=hover_text,
                    name=busi_type_labels.get(busi_type, busi_type),
                    showlegend=(node_info == type_nodes[0]),  # Only show legend for first node
                    legendgroup=busi_type,
                    opacity=0.8
                ),
                row=1, col=1
            )
            
            # Add text label (if width is sufficient, judged in seconds)
            if width > total_duration * 0.02:  # Width must be at least 2% of total duration
                display_label = busi_type_labels.get(node.busi_type, node.busi_type)
                label = f"{display_label}:{duration:.3f}"
                
                # If it's an AGENT, add agent_name or agent_id on a new line
                if node.busi_type == 'AGENT':
                    agent_info = None
                    if node.metadata and isinstance(node.metadata, dict):
                        # Try to get agent_name from metadata
                        agent_info = node.metadata.get('agent_name') or node.metadata.get('name')
                    # If no agent_name in metadata, use busi_id as agent_id
                    if not agent_info:
                        agent_info = node.busi_id
                    if agent_info:
                        label = f"{label}<br>{agent_info}"
                
                # if len(label) > 20:
                #     label = label[:17] + "..."
                annotations.append(dict(
                    x=(x_start + x_end) / 2,
                    y=y_pos,
                    text=label,
                    showarrow=False,
                    font=dict(size=8, color='black'),
                    xref='x',
                    yref='y'
                ))
    
    fig.update_layout(annotations=annotations)
    
    # Set main graph axes
    max_y = max(y_positions.values()) if y_positions else 0
    fig.update_xaxes(
        title_text=f'Time (seconds)',
        range=[0, total_duration],
        row=1, col=1
    )
    fig.update_yaxes(
        title_text='Stacking Layers',
        range=[-0.5, max_y + 0.5],
        row=1, col=1
    )
    
    # Add statistics table
    table_data = []
    table_data.append(['Statistics Item', 'Value'])
    table_data.append(['Total Nodes', str(stats['total_nodes'])])
    table_data.append(['Total Duration', f"{stats['total_duration']:.3f}s"])
    table_data.append(['Max Depth', str(stats['max_depth'])])
    table_data.append(['', ''])  # Empty row
    
    # Statistics by type
    table_data.append(['<b>Type Statistics</b>', '<b>Value</b>'])
    for busi_type, type_stats in sorted(stats['by_type'].items()):
        display_label = busi_type_labels.get(busi_type, busi_type)
        table_data.append([
            f"{display_label}",
            f"Count: {type_stats['count']}, "
            f"Total Duration: {type_stats['total_time']:.3f}s, "
            f"Average: {type_stats['avg_time']:.3f}s, "
            f"Max: {type_stats['max_time']:.3f}s, "
            f"Min: {type_stats['min_time']:.3f}s"
        ])
    
    fig.add_trace(
        go.Table(
            header=dict(
                values=['Statistics Item', 'Value'],
                fill_color='paleturquoise',
                align='left',
                font=dict(size=12, color='black')
            ),
            cells=dict(
                values=list(zip(*table_data)) if table_data else [[], []],
                fill_color='white',
                align='left',
                font=dict(size=10)
            )
        ),
        row=2, col=1
    )
    
    # Update overall layout
    fig.update_layout(
        title_text=f'Task {task_id} Execution Flame Graph and Statistics',
        height=800 + max_y * 30,
        showlegend=True,
        hovermode='closest'
    )
    
    # Save or display
    if output_path:
        # Ensure it's an HTML file
        if not output_path.endswith('.html'):
            output_path = output_path.replace('.png', '.html')
        fig.write_html(output_path)
        logging.info(f"Flame graph saved to: {output_path}")
    else:
        fig.show()

