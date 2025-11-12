#!/usr/bin/env python3
"""
火焰图分析工具
根据节点的execute_time和end_time绘制火焰图
支持交互式显示和正确处理时间重叠
"""
import logging
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from aworld.runners.state_manager import RunNode


def _check_overlap(interval1: Tuple[float, float], interval2: Tuple[float, float]) -> bool:
    """检查两个时间区间是否重叠"""
    start1, end1 = interval1
    start2, end2 = interval2
    return not (end1 <= start2 or end2 <= start1)


def _assign_y_positions(nodes: List[Dict], min_time: float) -> Dict[str, int]:
    """
    为节点分配y位置，处理时间重叠的情况
    先将AGENT和TOOL类型的节点固定在最底层（y=0），然后其他节点再按堆叠逻辑处理
    只有当时间区间真正重叠（有交集）时才堆叠，时间完全相同的节点放在同一层
    """
    # 分离AGENT/TOOL节点和其他节点
    agent_tool_nodes = []
    other_nodes = []
    
    for node_info in nodes:
        busi_type = node_info['node'].busi_type
        if busi_type in ['AGENT', 'TOOL']:
            agent_tool_nodes.append(node_info)
        else:
            other_nodes.append(node_info)
    
    # 对AGENT/TOOL节点按耗时从大到小排序
    agent_tool_nodes.sort(key=lambda n: (
        -(n['end_time'] - n['start_time']),  # 耗时从大到小
        n['start_time']  # 耗时相同时按开始时间排序
    ))
    
    # 对其他节点按耗时从大到小排序
    other_nodes.sort(key=lambda n: (
        -(n['end_time'] - n['start_time']),  # 耗时从大到小
        n['start_time']  # 耗时相同时按开始时间排序
    ))
    
    # 为每个节点分配y位置
    y_positions = {}
    # 每个元素是一个列表，包含该层上所有节点的 (node_id, start_time, end_time, duration) 元组
    occupied_layers = []
    
    # 第一步：处理AGENT和TOOL节点，固定在最底层（y=0）
    for node_info in agent_tool_nodes:
        node_id = node_info['node'].node_id
        start_time = node_info['start_time']
        end_time = node_info['end_time']
        duration = end_time - start_time
        
        # 直接放在最底层（y=0）
        y_positions[node_id] = 0
        # 如果最底层还没有初始化，初始化它
        if len(occupied_layers) == 0:
            occupied_layers.append([])
        occupied_layers[0].append((node_id, start_time, end_time, duration))
    
    # 第二步：处理其他节点，在已有层的基础上进行堆叠
    for node_info in other_nodes:
        node_id = node_info['node'].node_id
        start_time = node_info['start_time']
        end_time = node_info['end_time']
        duration = end_time - start_time
        current_interval = (start_time, end_time)
        
        # 从最底层开始查找可以放置的层
        layer_idx = None
        for idx, layer_nodes in enumerate(occupied_layers):
            # 检查该层上是否有节点与当前节点重叠
            has_overlap = False
            for other_node_id, other_start, other_end, other_duration in layer_nodes:
                other_interval = (other_start, other_end)
                # 如果时间完全相同，可以放在同一层
                if start_time == other_start and end_time == other_end:
                    continue
                # 检查是否有交集（真正重叠）
                if _check_overlap(current_interval, other_interval):
                    has_overlap = True
                    break
            
            # 如果该层上没有重叠的节点，可以放置在这里
            if not has_overlap:
                layer_idx = idx
                break
        
        # 如果没有找到可用的层，创建新层
        if layer_idx is None:
            layer_idx = len(occupied_layers)
            occupied_layers.append([])
        
        # 将节点放置在该层
        y_positions[node_id] = layer_idx
        occupied_layers[layer_idx].append((node_id, start_time, end_time, duration))
    
    return y_positions


def _calculate_statistics(nodes: List[RunNode]) -> Dict:
    """计算耗时统计信息"""
    stats = {
        'total_nodes': len(nodes),
        'by_type': defaultdict(lambda: {'count': 0, 'total_time': 0.0, 'avg_time': 0.0, 'max_time': 0.0, 'min_time': float('inf')}),
        'total_duration': 0.0,
        'max_depth': 0
    }
    
    # 计算全局时间范围
    valid_nodes = [n for n in nodes if n.execute_time and n.end_time]
    if not valid_nodes:
        return stats
    
    min_time = min(n.execute_time for n in valid_nodes)
    max_time = max(n.end_time for n in valid_nodes)
    stats['total_duration'] = max_time - min_time
    
    # 按类型统计
    for node in valid_nodes:
        duration = node.end_time - node.execute_time
        type_stats = stats['by_type'][node.busi_type]
        type_stats['count'] += 1
        type_stats['total_time'] += duration
        type_stats['max_time'] = max(type_stats['max_time'], duration)
        type_stats['min_time'] = min(type_stats['min_time'], duration)
    
    # 计算平均值
    for type_stats in stats['by_type'].values():
        if type_stats['count'] > 0:
            type_stats['avg_time'] = type_stats['total_time'] / type_stats['count']
        if type_stats['min_time'] == float('inf'):
            type_stats['min_time'] = 0.0
    
    # 计算最大深度（通过parent_node_id关系）
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
    根据节点的execute_time和end_time绘制交互式火焰图
    支持鼠标悬浮显示详细信息，正确处理时间重叠
    
    Args:
        nodes: 任务的所有节点列表
        task_id: 任务ID
        output_path: 输出路径（HTML文件），如果为None则显示图表
    """
    if not nodes:
        logging.warning(f"任务 {task_id} 没有节点数据，无法绘制火焰图")
        return
    
    # 过滤出有有效时间信息的节点
    valid_nodes = []
    for node in nodes:
        if node.execute_time and node.end_time and node.end_time > node.execute_time:
            valid_nodes.append(node)
    
    if not valid_nodes:
        logging.warning(f"任务 {task_id} 没有有效的节点时间数据，无法绘制火焰图")
        return
    
    # 计算全局时间范围
    min_time = min(node.execute_time for node in valid_nodes)
    max_time = max(node.end_time for node in valid_nodes)
    total_duration = max_time - min_time
    
    if total_duration <= 0:
        logging.warning(f"任务 {task_id} 的时间范围为0，无法绘制火焰图")
        return
    
    # 构建节点树结构，计算树深度
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
    
    # 准备节点数据
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
    
    # 分配y位置，处理重叠
    y_positions = _assign_y_positions(node_data, min_time)
    
    # 计算统计信息
    stats = _calculate_statistics(valid_nodes)
    
    # 为不同的busi_type设置颜色
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
        'HANDLER': '#2ECC71'
    }
    
    # 创建子图：主图和统计信息
    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.1,
        subplot_titles=('执行火焰图', '耗时统计'),
        specs=[[{"type": "scatter"}], [{"type": "table"}]]
    )
    
    # 使用填充区域绘制矩形，支持hover
    annotations = []
    
    # 定义绘制顺序：先绘制AGENT和TOOL（基础类型，应该在最下层），然后绘制其他类型
    draw_order = ['AGENT', 'TOOL'] + [t for t in busi_type_colors.keys() if t not in ['AGENT', 'TOOL']]
    
    # 按类型分组绘制，先绘制AGENT和TOOL
    for busi_type in draw_order:
        type_nodes = [nd for nd in node_data if nd['node'].busi_type == busi_type]
        if not type_nodes:
            continue
        
        color = busi_type_colors[busi_type]
        
        # 为每个节点创建矩形（使用填充区域）
        for node_info in type_nodes:
            node = node_info['node']
            start_time = node_info['start_time']
            end_time = node_info['end_time']
            duration = node_info['duration']
            
            x_start = (start_time - min_time) / total_duration
            x_end = (end_time - min_time) / total_duration
            width = x_end - x_start
            
            y_pos = y_positions[node.node_id]
            y_bottom = y_pos - 0.4
            y_top = y_pos + 0.4
            
            # 准备hover信息
            hover_text = (
                f"<b>asd {duration:.3f}s {node.busi_type if node.busi_type != 'MEMORY' else 'HISTORY'}: {node.busi_id}</b><br>"
            )
            
            # 使用填充区域绘制矩形（顺时针绘制矩形四个顶点）
            fig.add_trace(
                go.Scatter(
                    x=[x_start, x_end, x_end, x_start, x_start],
                    y=[y_bottom, y_bottom, y_top, y_top, y_bottom],
                    mode='lines',
                    fill='toself',
                    fillcolor=color,
                    line=dict(color='black', width=1),
                    hovertemplate=hover_text + '<extra></extra>',
                    name=busi_type,
                    showlegend=(node_info == type_nodes[0]),  # 只为第一个节点显示图例
                    legendgroup=busi_type,
                    opacity=0.8
                ),
                row=1, col=1
            )
            
            # 添加文本标签（如果宽度足够）
            if width > 0.02:
                label = f"{node.busi_type}:{node.busi_id}"
                if len(label) > 20:
                    label = label[:17] + "..."
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
    
    # 设置主图坐标轴
    max_y = max(y_positions.values()) if y_positions else 0
    fig.update_xaxes(
        title_text=f'时间 (总时长: {total_duration:.2f}秒)',
        range=[0, 1],
        row=1, col=1
    )
    fig.update_yaxes(
        title_text='堆叠层数',
        range=[-0.5, max_y + 0.5],
        row=1, col=1
    )
    
    # 添加统计表格
    table_data = []
    table_data.append(['统计项', '数值'])
    table_data.append(['总节点数', str(stats['total_nodes'])])
    table_data.append(['总耗时', f"{stats['total_duration']:.3f}s"])
    table_data.append(['最大深度', str(stats['max_depth'])])
    table_data.append(['', ''])  # 空行
    
    # 按类型统计
    table_data.append(['<b>类型统计</b>', '<b>数值</b>'])
    for busi_type, type_stats in sorted(stats['by_type'].items()):
        table_data.append([
            f"{busi_type}",
            f"数量: {type_stats['count']}, "
            f"总耗时: {type_stats['total_time']:.3f}s, "
            f"平均: {type_stats['avg_time']:.3f}s, "
            f"最大: {type_stats['max_time']:.3f}s, "
            f"最小: {type_stats['min_time']:.3f}s"
        ])
    
    fig.add_trace(
        go.Table(
            header=dict(
                values=['统计项', '数值'],
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
    
    # 更新整体布局
    fig.update_layout(
        title_text=f'任务 {task_id} 执行火焰图与统计',
        height=800 + max_y * 30,
        showlegend=True,
        hovermode='closest'
    )
    
    # 保存或显示
    if output_path:
        # 确保是HTML文件
        if not output_path.endswith('.html'):
            output_path = output_path.replace('.png', '.html')
        fig.write_html(output_path)
        logging.info(f"火焰图已保存到: {output_path}")
    else:
        fig.show()

