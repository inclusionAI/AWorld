#!/usr/bin/env python3
"""
分析轨迹文件中的工具调用和LLM调用的耗时分布
"""
import json
import ast
import os
import glob
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Any, Tuple
import statistics
import matplotlib.pyplot as plt
import matplotlib
import seaborn as sns

# 设置中文字体
try:
    import matplotlib.font_manager as fm
    # 获取所有可用字体
    available_fonts = [f.name for f in fm.fontManager.ttflist]
    # 优先使用的中文字体列表
    preferred_fonts = ['PingFang SC', 'STHeiti', 'SimHei', 'Microsoft YaHei', 
                       'Arial Unicode MS', 'WenQuanYi Micro Hei', 'Noto Sans CJK SC']
    
    # 查找第一个可用的中文字体
    chinese_font = None
    for font in preferred_fonts:
        if font in available_fonts:
            chinese_font = font
            break
    
    if chinese_font:
        matplotlib.rcParams['font.sans-serif'] = [chinese_font] + matplotlib.rcParams['font.sans-serif']
        print(f"使用字体: {chinese_font}")
    else:
        # 如果没有找到中文字体，使用英文标签
        print("未找到中文字体，将使用英文标签")
        matplotlib.rcParams['font.sans-serif'] = ['DejaVu Sans']
except Exception as e:
    print(f"字体设置失败: {e}，将使用默认字体")
    matplotlib.rcParams['font.sans-serif'] = ['DejaVu Sans']

matplotlib.rcParams['axes.unicode_minus'] = False
sns.set_style("whitegrid")
sns.set_palette("husl")

def parse_time(time_str: str) -> datetime:
    """解析时间字符串"""
    return datetime.fromisoformat(time_str)

def calculate_duration(start_time: str, end_time: str) -> float:
    """计算耗时（秒）"""
    start = parse_time(start_time)
    end = parse_time(end_time)
    return (end - start).total_seconds()

def analyze_single_trajectory(file_path: str, silent: bool = False):
    """分析单个轨迹文件，返回统计数据"""
    with open(file_path, 'r', encoding='utf-8') as f:
        # 读取文件内容，因为文件可能是Python字典格式而不是标准JSON
        content = f.read()
        # 尝试使用ast.literal_eval解析Python字典格式
        try:
            data = ast.literal_eval(content)
        except:
            # 如果不是Python格式，尝试JSON
            data = json.loads(content)
    
    llm_durations = []  # LLM调用耗时
    tool_durations = []  # 工具调用耗时
    tool_type_durations = defaultdict(list)  # 按工具类型分类的耗时
    
    # 遍历所有条目
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
            
        metadata = entry.get('metadata', {})
        role = entry.get('role', '')
        start_time = metadata.get('start_time')
        end_time = metadata.get('end_time')
        
        if not start_time or not end_time:
            continue
        
        duration = calculate_duration(start_time, end_time)
        
        # 判断是LLM调用还是工具调用
        if role == 'assistant':
            # assistant角色且没有tool_call_id的是LLM调用
            tool_call_id = metadata.get('tool_call_id')
            if not tool_call_id:
                llm_durations.append(duration)
        elif role == 'tool':
            # tool角色是工具调用
            tool_durations.append(duration)
            # 获取工具名称
            ext_info = metadata.get('ext_info', {})
            tool_name = ext_info.get('tool_name', 'unknown')
            action_name = ext_info.get('action_name', 'unknown')
            tool_type = f"{tool_name}.{action_name}"
            tool_type_durations[tool_type].append(duration)
    
    # 返回数据
    result_data = {
        'llm_durations': llm_durations,
        'tool_durations': tool_durations,
        'tool_type_durations': tool_type_durations,
        'total_llm_time': sum(llm_durations) if llm_durations else 0,
        'total_tool_time': sum(tool_durations) if tool_durations else 0,
        'llm_count': len(llm_durations),
        'tool_count': len(tool_durations)
    }
    
    if not silent:
        # 统计信息
        print("=" * 80)
        print(f"耗时分布统计 - {os.path.basename(file_path)}")
        print("=" * 80)
        
        print(f"\n📊 总体统计:")
        print(f"  LLM调用次数: {result_data['llm_count']}")
        print(f"  工具调用次数: {result_data['tool_count']}")
        print(f"  总调用次数: {result_data['llm_count'] + result_data['tool_count']}")
        
        if llm_durations:
            print(f"\n🤖 LLM调用耗时统计（秒）:")
            print(f"  总耗时: {result_data['total_llm_time']:.2f}")
            print(f"  平均耗时: {statistics.mean(llm_durations):.2f}")
            print(f"  中位数耗时: {statistics.median(llm_durations):.2f}")
            print(f"  最小耗时: {min(llm_durations):.2f}")
            print(f"  最大耗时: {max(llm_durations):.2f}")
            if len(llm_durations) > 1:
                print(f"  标准差: {statistics.stdev(llm_durations):.2f}")
        
        if tool_durations:
            print(f"\n🛠️  工具调用耗时统计（秒）:")
            print(f"  总耗时: {result_data['total_tool_time']:.2f}")
            print(f"  平均耗时: {statistics.mean(tool_durations):.2f}")
            print(f"  中位数耗时: {statistics.median(tool_durations):.2f}")
            print(f"  最小耗时: {min(tool_durations):.2f}")
            print(f"  最大耗时: {max(tool_durations):.2f}")
            if len(tool_durations) > 1:
                print(f"  标准差: {statistics.stdev(tool_durations):.2f}")
        
        total_time = result_data['total_llm_time'] + result_data['total_tool_time']
        if total_time > 0:
            print(f"\n📈 耗时占比:")
            print(f"  LLM调用占比: {result_data['total_llm_time']/total_time*100:.2f}% ({result_data['total_llm_time']:.2f}秒)")
            print(f"  工具调用占比: {result_data['total_tool_time']/total_time*100:.2f}% ({result_data['total_tool_time']:.2f}秒)")
            print(f"  总耗时: {total_time:.2f}秒")
        
        print("\n" + "=" * 80)
    
    return result_data

def plot_timing_analysis(data: Dict, output_path: str = None):
    """生成耗时分析图表"""
    # 支持两种数据格式：完整数据或汇总数据
    if 'total_time' in data:
        # 汇总数据（来自目录分析）
        total_llm_time = data.get('total_llm_time', 0)
        total_tool_time = data.get('total_tool_time', 0)
        total_time = data.get('total_time', total_llm_time + total_tool_time)
        llm_count = data.get('llm_count', 0)
        tool_count = data.get('tool_count', 0)
    else:
        # 完整数据（来自单个文件分析）
        llm_durations = data.get('llm_durations', [])
        tool_durations = data.get('tool_durations', [])
        total_llm_time = sum(llm_durations) if llm_durations else 0
        total_tool_time = sum(tool_durations) if tool_durations else 0
        total_time = total_llm_time + total_tool_time
        llm_count = len(llm_durations)
        tool_count = len(tool_durations)
    
    # 创建单个柱状图
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # 准备数据：任务总耗时、LLM调用总耗时、工具调用总耗时
    categories = ['Total Task', 'LLM Calls', 'Tool Calls']
    times = [total_time, total_llm_time, total_tool_time]
    counts = [llm_count + tool_count, llm_count, tool_count]
    
    # 创建标签，Total Task不加括号，其他加上调用次数（带"calls"）
    labels = []
    labels.append('Total Task')  # Total Task不加括号
    labels.append(f'LLM Calls ({llm_count} calls)')
    labels.append(f'Tool Calls ({tool_count} calls)')
    
    x_pos = range(len(categories))
    width = 0.6
    
    # 使用不同颜色
    colors = ['#FFA07A', '#FF6B6B', '#4ECDC4']
    
    bars = ax.bar(x_pos, times, width, color=colors, alpha=0.8, 
                  edgecolor='black', linewidth=1.2)
    
    ax.set_xlabel('Type', fontsize=12, fontweight='bold')
    ax.set_ylabel('Total Time (seconds)', fontsize=12, fontweight='bold')
    ax.set_title('Timing Analysis Report', fontsize=14, fontweight='bold')
    ax.set_xticks(x_pos)
    ax.set_xticklabels(labels, fontsize=11)
    ax.grid(axis='y', alpha=0.3)
    
    # 添加数值标签
    for bar, time in zip(bars, times):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{time:.1f}s', ha='center', va='bottom',
                fontsize=10, fontweight='bold')
    
    # 保存图表
    if output_path is None:
        output_path = 'timing_analysis.png'
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight', facecolor='white')
    print(f"\n📊 图表已保存到: {output_path}")
    plt.close()

def analyze_directory(directory_path: str, generate_plot: bool = True):
    """分析目录下所有traj_*.json文件，计算平均值"""
    # 查找所有traj_*.json文件
    pattern = os.path.join(directory_path, 'traj_*.json')
    traj_files = glob.glob(pattern)
    
    if not traj_files:
        print(f"在目录 {directory_path} 中未找到 traj_*.json 文件")
        return None
    
    print(f"找到 {len(traj_files)} 个轨迹文件")
    print(f"开始分析...\n")
    
    # 收集所有文件的数据
    all_results = []
    for traj_file in sorted(traj_files):
        try:
            result = analyze_single_trajectory(traj_file, silent=True)
            all_results.append(result)
            print(f"✓ 已处理: {os.path.basename(traj_file)}")
        except Exception as e:
            print(f"✗ 处理失败 {os.path.basename(traj_file)}: {e}")
            continue
    
    if not all_results:
        print("没有成功处理任何文件")
        return None
    
    # 计算平均值
    num_files = len(all_results)
    avg_total_llm_time = sum(r['total_llm_time'] for r in all_results) / num_files
    avg_total_tool_time = sum(r['total_tool_time'] for r in all_results) / num_files
    avg_total_time = avg_total_llm_time + avg_total_tool_time
    avg_llm_count = sum(r['llm_count'] for r in all_results) / num_files
    avg_tool_count = sum(r['tool_count'] for r in all_results) / num_files
    
    # 打印统计信息
    print("\n" + "=" * 80)
    print(f"平均统计结果 (基于 {num_files} 个文件)")
    print("=" * 80)
    
    print(f"\n📊 平均统计:")
    print(f"  平均LLM调用次数: {avg_llm_count:.2f}")
    print(f"  平均工具调用次数: {avg_tool_count:.2f}")
    print(f"  平均总调用次数: {avg_llm_count + avg_tool_count:.2f}")
    
    print(f"\n📈 平均耗时:")
    print(f"  平均LLM调用总耗时: {avg_total_llm_time:.2f}秒")
    print(f"  平均工具调用总耗时: {avg_total_tool_time:.2f}秒")
    print(f"  平均任务总耗时: {avg_total_time:.2f}秒")
    
    if avg_total_time > 0:
        print(f"\n📈 平均耗时占比:")
        print(f"  LLM调用占比: {avg_total_llm_time/avg_total_time*100:.2f}% ({avg_total_llm_time:.2f}秒)")
        print(f"  工具调用占比: {avg_total_tool_time/avg_total_time*100:.2f}% ({avg_total_tool_time:.2f}秒)")
    
    print("\n" + "=" * 80)
    
    # 准备图表数据
    chart_data = {
        'llm_durations': [],  # 这里不需要，但保持接口一致
        'tool_durations': [],
        'tool_type_durations': {},
        'tool_stats': [],
        'total_llm_time': avg_total_llm_time,
        'total_tool_time': avg_total_tool_time,
        'total_time': avg_total_time,
        'llm_count': int(round(avg_llm_count)),
        'tool_count': int(round(avg_tool_count))
    }
    
    # 生成图表
    if generate_plot:
        output_path = os.path.join(directory_path, 'avg_timing_analysis.png')
        plot_timing_analysis(chart_data, output_path)
    
    return chart_data

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法: python analyze_timing.py <directory_path> [--no-plot]")
        print("      扫描目录下所有 traj_*.json 文件并计算平均值")
        sys.exit(1)
    
    path = sys.argv[1]
    generate_plot = '--no-plot' not in sys.argv
    
    # 判断是文件还是目录
    if os.path.isfile(path):
        # 单个文件模式（向后兼容）
        analyze_single_trajectory(path, silent=False)
        if generate_plot:
            base_name = os.path.splitext(os.path.basename(path))[0]
            output_dir = os.path.dirname(path)
            output_path = os.path.join(output_dir, f'{base_name}_timing_analysis.png')
            result = analyze_single_trajectory(path, silent=True)
            plot_timing_analysis(result, output_path)
    elif os.path.isdir(path):
        # 目录模式
        analyze_directory(path, generate_plot=generate_plot)
    else:
        print(f"错误: {path} 不是有效的文件或目录")
        sys.exit(1)

