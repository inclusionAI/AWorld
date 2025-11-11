#!/usr/bin/env python3
"""
分析轨迹文件中的工具调用和LLM调用的耗时分布
"""
import json
import ast
import os
import glob
import re
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
except Exception:
    # 静默使用默认字体
    matplotlib.rcParams['font.sans-serif'] = ['DejaVu Sans']

matplotlib.rcParams['axes.unicode_minus'] = False
sns.set_style("whitegrid")
sns.set_palette("husl")

def parse_time(time_str: str) -> datetime:
    """解析时间字符串"""
    return datetime.fromisoformat(time_str)

def calculate_duration(start_time: str, end_time: str) -> float:
    """计算耗时（秒），确保返回正值"""
    start = parse_time(start_time)
    end = parse_time(end_time)
    duration = (end - start).total_seconds()
    # 如果计算出来是负数，取绝对值（可能是时间戳顺序问题）
    return abs(duration)

def detect_blocking_issues(content_str: str) -> Dict[str, Any]:
    """检测内容中的拦截问题（登录、验证码、反爬虫）
    
    返回:
        {
            'is_blocked': bool,
            'block_reasons': List[str],  # 所有拦截原因列表
            'has_answer': bool,
            'login_blocked': bool,
            'captcha_blocked': bool,
            'anti_bot_blocked': bool
        }
    """
    content_lower = content_str.lower()
    
    # 检查是否有 <answer> 标签
    has_answer = False
    answer_match = re.search(r'<answer>(.*?)</answer>', content_str, re.IGNORECASE | re.DOTALL)
    if answer_match:
        answer_content = answer_match.group(1).strip()
        # 只要有answer标签就算有答案
        if answer_content or True:
            has_answer = True
    
    # 检查拦截关键词
    blocked_keywords = {
        'login': [r'登录', r'login', r'sign\s*in', r'需要登录', r'请登录', r'未登录', r'请先登录'],
        'captcha': [r'验证码', r'captcha', r'verification', r'人机验证', r'安全验证', r'请完成验证', 
                   r'拖动滑块', r'drag.*slider', r'please drag', r'验证码验证', r'验证码弹窗', r'验证码要求'],
        'anti_bot': [r'反爬虫', r'anti.*bot', r'blocked', r'forbidden', r'\b403\b', r'\b429\b', 
                    r'访问被拒绝', r'请求过于频繁', r'rate limit', r'访问受限'],
    }
    
    block_reasons = []
    login_blocked = False
    captcha_blocked = False
    anti_bot_blocked = False
    
    for reason, patterns in blocked_keywords.items():
        for pattern in patterns:
            if re.search(pattern, content_lower, re.IGNORECASE):
                if reason not in block_reasons:
                    block_reasons.append(reason)
                if reason == 'login':
                    login_blocked = True
                elif reason == 'captcha':
                    captcha_blocked = True
                elif reason == 'anti_bot':
                    anti_bot_blocked = True
                break
    
    is_blocked = len(block_reasons) > 0
    
    return {
        'is_blocked': is_blocked,
        'block_reasons': block_reasons,
        'has_answer': has_answer,
        'login_blocked': login_blocked,
        'captcha_blocked': captcha_blocked,
        'anti_bot_blocked': anti_bot_blocked
    }

def analyze_single_trajectory(file_path: str, silent: bool = False):
    """分析单个轨迹文件，返回统计数据"""
    with open(file_path, 'r', encoding='utf-8') as f:
        # 读取文件内容，因为文件可能是Python字典格式而不是标准JSON
        content = f.read()
        # 尝试使用ast.literal_eval解析Python字典格式
        try:
            data = ast.literal_eval(content)
        except (ValueError, SyntaxError):
            # 如果不是Python格式，尝试JSON
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                raise ValueError(f"无法解析文件 {file_path}: 既不是有效的Python字典也不是JSON格式") from e
    
    # 检测拦截问题
    content_str = str(data)
    blocking_info = detect_blocking_issues(content_str)
    
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
        'tool_count': len(tool_durations),
        'blocking_info': blocking_info  # 添加拦截信息
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

def extract_timing_data(data: Dict) -> Dict:
    """从数据字典中提取耗时和调用次数信息"""
    if 'total_time' in data:
        # 汇总数据（来自目录分析）
        total_llm_time = data.get('total_llm_time', 0)
        total_tool_time = data.get('total_tool_time', 0)
        total_time = data.get('total_time', total_llm_time + total_tool_time)
        llm_count = int(round(data.get('llm_count', 0)))
        tool_count = int(round(data.get('tool_count', 0)))
    else:
        # 完整数据（来自单个文件分析）
        llm_durations = data.get('llm_durations', [])
        tool_durations = data.get('tool_durations', [])
        total_llm_time = sum(llm_durations) if llm_durations else 0
        total_tool_time = sum(tool_durations) if tool_durations else 0
        total_time = total_llm_time + total_tool_time
        llm_count = len(llm_durations)
        tool_count = len(tool_durations)
    
    return {
        'total_time': total_time,
        'total_llm_time': total_llm_time,
        'total_tool_time': total_tool_time,
        'llm_count': llm_count,
        'tool_count': tool_count
    }

def plot_single_bar_chart(ax, timing_data: Dict, title: str = 'Timing Analysis Report', is_average: bool = False):
    """在指定的axes上绘制单个柱状图
    
    Args:
        ax: matplotlib axes对象
        timing_data: 包含耗时数据的字典
        title: 图表标题
        is_average: 是否为平均值数据（True表示显示平均值，False表示显示总值）
    """
    total_time = timing_data['total_time']
    total_llm_time = timing_data['total_llm_time']
    total_tool_time = timing_data['total_tool_time']
    llm_count = timing_data['llm_count']
    tool_count = timing_data['tool_count']
    
    # 准备数据：任务平均耗时、LLM调用平均耗时、工具调用平均耗时
    categories = ['Total Task', 'LLM Calls', 'Tool Calls']
    times = [total_time, total_llm_time, total_tool_time]
    counts = [llm_count + tool_count, llm_count, tool_count]
    
    # 创建标签，Total Task不加括号，其他加上调用次数（带"calls"）
    labels = ['Total Task']
    labels.append(f'LLM Calls ({llm_count} calls)')
    labels.append(f'Tool Calls ({tool_count} calls)')
    
    x_pos = range(len(categories))
    width = 0.6
    colors = ['#FFA07A', '#FF6B6B', '#4ECDC4']
    
    bars = ax.bar(x_pos, times, width, color=colors, alpha=0.8, 
                  edgecolor='black', linewidth=1.2)
    
    ax.set_xlabel('Type', fontsize=12, fontweight='bold')
    # 根据是否为平均值设置Y轴标签
    ylabel = 'Average Time (seconds)' if is_average else 'Total Time (seconds)'
    ax.set_ylabel(ylabel, fontsize=12, fontweight='bold')
    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.set_xticks(x_pos)
    ax.set_xticklabels(labels, fontsize=11)
    ax.grid(axis='y', alpha=0.3)
    
    # 添加数值标签
    for bar, time in zip(bars, times):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{time:.1f}s', ha='center', va='bottom',
                fontsize=10, fontweight='bold')
    
    return max(times)  # 返回最大耗时，用于统一y轴

def plot_timing_analysis(data: Dict, output_path: str = None):
    """生成耗时分析图表，包含拦截统计"""
    timing_data = extract_timing_data(data)
    blocking_stats = data.get('blocking_stats', None)
    
    # 判断是否为平均值数据（如果有blocking_stats，说明是从目录分析得到的平均值）
    is_average = blocking_stats is not None
    
    # 如果有拦截统计，创建2个子图（1行2列），否则只创建1个
    if blocking_stats:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 6))
        
        # 第一个子图：耗时分析（平均值）
        plot_single_bar_chart(ax1, timing_data, 'Timing Analysis Report', is_average=True)
        
        # 第二个子图：拦截统计
        plot_blocking_stats(ax2, blocking_stats)
        
        # 添加总标题
        fig.suptitle('Timing and Blocking Analysis Report', fontsize=16, fontweight='bold', y=1.02)
    else:
        # 没有拦截统计，只显示耗时分析（可能是单个文件，显示总值）
        fig, ax1 = plt.subplots(figsize=(10, 6))
        plot_single_bar_chart(ax1, timing_data, 'Timing Analysis Report', is_average=False)
    
    # 保存图表
    if output_path is None:
        output_path = 'timing_analysis.png'
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight', facecolor='white')
    print(f"\n📊 图表已保存到: {output_path}")
    plt.close()

def plot_blocking_stats(ax, blocking_stats: Dict):
    """在指定的axes上绘制拦截统计柱状图"""
    total_files = blocking_stats.get('total_files', 0)
    total_has_answer = blocking_stats.get('total_has_answer', 0)
    login_count = blocking_stats.get('login_blocked_count', 0)
    captcha_count = blocking_stats.get('captcha_blocked_count', 0)
    anti_bot_count = blocking_stats.get('anti_bot_blocked_count', 0)
    
    # 准备数据：只显示 Has Answer 和拦截类型统计
    categories = ['Has Answer', 'Login', 'Captcha', 'Anti-Bot']
    values = [total_has_answer, login_count, captcha_count, anti_bot_count]
    colors = ['#4ECDC4', '#FF6B6B', '#FFA07A', '#FF4757']
    
    x_pos = range(len(categories))
    width = 0.6
    
    # 绘制柱状图
    bars = ax.bar(x_pos, values, width, color=colors, alpha=0.8, 
                  edgecolor='black', linewidth=1.2)
    
    # 设置标签
    ax.set_xticks(x_pos)
    ax.set_xticklabels(categories, fontsize=10, rotation=15, ha='right')
    
    ax.set_ylabel('Count', fontsize=12, fontweight='bold')
    ax.set_title('Blocking Statistics', fontsize=14, fontweight='bold')
    ax.grid(axis='y', alpha=0.3)
    
    # 添加数值标签
    for bar in bars:
        height = bar.get_height()
        if height > 0:
            ax.text(bar.get_x() + bar.get_width()/2., height,
                    f'{int(height)}', ha='center', va='bottom',
                    fontsize=9, fontweight='bold')
    
    # 添加总数标注
    ax.text(0.02, 0.98, f'Total Files: {total_files}', 
            transform=ax.transAxes, fontsize=10, 
            verticalalignment='top', bbox=dict(boxstyle='round', 
            facecolor='wheat', alpha=0.5))

def plot_multi_level_analysis(level_data_list: List[Dict], output_path: str = None):
    """生成多Level对比图表，每个Level一个柱状图"""
    if len(level_data_list) != 3:
        raise ValueError("需要提供3个Level的数据")
    
    # 提取所有Level的数据
    timing_data_list = [extract_timing_data(data) for data in level_data_list]
    
    # 计算所有Level的最大耗时，用于统一y轴范围
    max_time = max(td['total_time'] for td in timing_data_list)
    y_max = max_time * 1.15  # 留15%的顶部空间
    
    # 创建3个子图：1行3列
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    level_titles = ['Level 1', 'Level 2', 'Level 3']
    
    for idx, (timing_data, ax) in enumerate(zip(timing_data_list, axes)):
        # 绘制柱状图（多Level对比通常是平均值）
        plot_single_bar_chart(ax, timing_data, level_titles[idx], is_average=True)
        
        # 统一y轴范围
        ax.set_ylim(0, y_max)
        
        # 调整标签角度以适应3个子图布局
        ax.set_xticklabels(ax.get_xticklabels(), rotation=15, ha='right', fontsize=10)
        ax.set_xlabel('Type', fontsize=11, fontweight='bold')
        # Y轴标签已经在plot_single_bar_chart中设置，这里不需要重复设置
    
    # 添加总标题
    fig.suptitle('Multi-Level Timing Analysis', fontsize=16, fontweight='bold', y=1.02)
    
    # 保存图表
    if output_path is None:
        output_path = 'multi_level_timing_analysis.png'
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight', facecolor='white')
    print(f"\n📊 多Level对比图表已保存到: {output_path}")
    plt.close()

def parse_digest_log(log_file_path: str, level_id: str) -> List[float]:
    """从digest log文件中解析指定level_id的所有任务执行耗时"""
    task_durations = []
    
    try:
        with open(log_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # 解析格式: eval_task_digest|level_id|task_id|duration|usage_dict
                parts = line.split('|')
                if len(parts) >= 4 and parts[0] == 'eval_task_digest':
                    log_level_id = parts[1]
                    if log_level_id == level_id:
                        try:
                            duration = float(parts[3])
                            task_durations.append(duration)
                        except (ValueError, IndexError):
                            continue
    except FileNotFoundError:
        print(f"错误: 找不到log文件 {log_file_path}")
        return []
    except Exception as e:
        print(f"错误: 读取log文件时出错: {e}")
        return []
    
    return task_durations

def analyze_level_from_log(log_file_path: str, level_id: str, trajectory_dir: str = None) -> Dict:
    """从log文件和trajectory目录分析level数据
    
    Total Task耗时: 从log文件中统计该level_id的所有任务的平均耗时
    LLM Calls和Tool Calls耗时: 从trajectory目录中统计每个任务的平均值
    LLM和Tool调用次数: 从trajectory目录中统计每个任务的平均调用次数
    """
    # 1. 从log文件获取任务总耗时（这是Total Task的耗时）
    task_durations = parse_digest_log(log_file_path, level_id)
    
    if not task_durations:
        print(f"警告: 在log文件中未找到 level_id {level_id} 的任务数据")
        return None
    
    # 计算任务平均总耗时（用于Total Task）
    avg_task_time = sum(task_durations) / len(task_durations)
    
    # 2. 从trajectory目录获取LLM和工具调用耗时及平均调用次数
    # 每个level都有自己独立的trajectory目录，分别计算平均值
    if trajectory_dir and os.path.isdir(trajectory_dir):
        trajectory_data = analyze_directory(trajectory_dir, generate_plot=False)
        if trajectory_data:
            # analyze_directory返回的已经是平均值（基于该level目录下的所有traj文件）
            avg_llm_time = trajectory_data.get('total_llm_time', 0)
            avg_tool_time = trajectory_data.get('total_tool_time', 0)
            # llm_count和tool_count已经是该level的平均调用次数（四舍五入后的整数）
            avg_llm_count = trajectory_data.get('llm_count', 0)
            avg_tool_count = trajectory_data.get('tool_count', 0)
        else:
            print(f"  警告: 无法从trajectory目录获取数据，LLM和Tool耗时设为0")
            avg_llm_time = 0
            avg_tool_time = 0
            avg_llm_count = 0
            avg_tool_count = 0
    else:
        print(f"  警告: 未找到trajectory目录，LLM和Tool耗时设为0")
        avg_llm_time = 0
        avg_tool_time = 0
        avg_llm_count = 0
        avg_tool_count = 0
    
    return {
        'total_time': avg_task_time,  # 来自log文件，所有任务的平均耗时
        'total_llm_time': avg_llm_time,  # 来自trajectory目录，每个任务的平均LLM耗时
        'total_tool_time': avg_tool_time,  # 来自trajectory目录，每个任务的平均工具耗时
        'llm_count': avg_llm_count,  # 来自trajectory目录，每个任务的平均LLM调用次数
        'tool_count': avg_tool_count,  # 来自trajectory目录，每个任务的平均工具调用次数
        'task_count': len(task_durations)
    }

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
    
    # 调用次数取整（因为平均值可能为小数）
    avg_llm_count_int = int(round(avg_llm_count))
    avg_tool_count_int = int(round(avg_tool_count))
    
    # 汇总拦截统计
    total_blocked = sum(1 for r in all_results if r.get('blocking_info', {}).get('is_blocked', False))
    total_has_answer = sum(1 for r in all_results if r.get('blocking_info', {}).get('has_answer', False))
    blocked_but_has_answer = sum(1 for r in all_results 
                                 if r.get('blocking_info', {}).get('is_blocked', False) 
                                 and r.get('blocking_info', {}).get('has_answer', False))
    blocked_no_answer = total_blocked - blocked_but_has_answer
    
    # 统计各种拦截类型
    login_blocked_count = sum(1 for r in all_results if r.get('blocking_info', {}).get('login_blocked', False))
    captcha_blocked_count = sum(1 for r in all_results if r.get('blocking_info', {}).get('captcha_blocked', False))
    anti_bot_blocked_count = sum(1 for r in all_results if r.get('blocking_info', {}).get('anti_bot_blocked', False))
    
    # 打印统计信息
    print("\n" + "=" * 80)
    print(f"平均统计结果 (基于 {num_files} 个文件)")
    print("=" * 80)
    
    print(f"\n📊 平均统计:")
    print(f"  平均LLM调用次数: {avg_llm_count:.2f} (约 {avg_llm_count_int})")
    print(f"  平均工具调用次数: {avg_tool_count:.2f} (约 {avg_tool_count_int})")
    print(f"  平均总调用次数: {avg_llm_count + avg_tool_count:.2f} (约 {avg_llm_count_int + avg_tool_count_int})")
    
    print(f"\n📈 平均耗时:")
    print(f"  平均LLM调用总耗时: {avg_total_llm_time:.2f}秒")
    print(f"  平均工具调用总耗时: {avg_total_tool_time:.2f}秒")
    print(f"  平均任务总耗时: {avg_total_time:.2f}秒")
    
    if avg_total_time > 0:
        print(f"\n📈 平均耗时占比:")
        print(f"  LLM调用占比: {avg_total_llm_time/avg_total_time*100:.2f}% ({avg_total_llm_time:.2f}秒)")
        print(f"  工具调用占比: {avg_total_tool_time/avg_total_time*100:.2f}% ({avg_total_tool_time:.2f}秒)")
    
    # 打印拦截统计
    print(f"\n🚫 拦截统计:")
    print(f"  正常产出 <answer> 的数量: {total_has_answer} ({total_has_answer/num_files*100:.1f}%)")
    print(f"  被拦截影响的数量: {total_blocked} ({total_blocked/num_files*100:.1f}%)")
    print(f"    其中：被拦截但仍产出答案: {blocked_but_has_answer} ({blocked_but_has_answer/num_files*100:.1f}%)")
    print(f"    其中：被拦截且未产出答案: {blocked_no_answer} ({blocked_no_answer/num_files*100:.1f}%)")
    print(f"  正常完成（有答案且未被拦截）: {total_has_answer - blocked_but_has_answer} ({(total_has_answer - blocked_but_has_answer)/num_files*100:.1f}%)")
    print(f"\n  拦截原因统计（一个文件可能同时有多个拦截原因）:")
    print(f"    登录拦截: {login_blocked_count} 次")
    print(f"    验证码拦截: {captcha_blocked_count} 次")
    print(f"    反爬虫拦截: {anti_bot_blocked_count} 次")
    
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
        'llm_count': avg_llm_count_int,
        'tool_count': avg_tool_count_int,
        'blocking_stats': {
            'total_files': num_files,
            'total_has_answer': total_has_answer,
            'total_blocked': total_blocked,
            'blocked_but_has_answer': blocked_but_has_answer,
            'blocked_no_answer': blocked_no_answer,
            'login_blocked_count': login_blocked_count,
            'captcha_blocked_count': captcha_blocked_count,
            'anti_bot_blocked_count': anti_bot_blocked_count
        }
    }
    
    # 生成图表
    if generate_plot:
        output_path = os.path.join(directory_path, 'avg_timing_analysis.png')
        plot_timing_analysis(chart_data, output_path)
    
    return chart_data

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法:")
        print("  1. 单个文件: python analyze_timing.py <file_path> [--no-plot]")
        print("  2. 单个目录（推荐）: python analyze_timing.py <directory_path> [--no-plot]")
        print("     分析目录下所有traj_*.json文件，包含耗时分析和拦截统计")
        print("  3. 3个Level对比(目录): python analyze_timing.py <dir1> <dir2> <dir3> [output_path]")
        print("  4. 3个Level对比(Log): python analyze_timing.py --log <log_file> <level_id1> <level_id2> <level_id3> [traj_base_dir] [output_path]")
        sys.exit(1)
    
    # 过滤掉--no-plot参数
    args = [arg for arg in sys.argv[1:] if arg != '--no-plot']
    generate_plot = '--no-plot' not in sys.argv
    
    # 检查是否是log文件模式
    if args[0] == '--log' and len(args) >= 5:
        # Log文件模式: --log <log_file> <level_id1> <level_id2> <level_id3> [traj_base_dir] [output_path]
        log_file = args[1]
        level_id1 = args[2]
        level_id2 = args[3]
        level_id3 = args[4]
        
        # 可选的trajectory基础目录（trajectory目录名就是level_id）
        traj_base_dir = None
        output_path = None
        
        if len(args) > 5:
            # 检查第5个参数是trajectory基础目录还是output_path
            if os.path.isdir(args[5]):
                traj_base_dir = args[5]
                if len(args) > 6:
                    output_path = args[6]
            else:
                output_path = args[5]
        
        if not os.path.isfile(log_file):
            print(f"错误: log文件不存在: {log_file}")
            sys.exit(1)
        
        print("=" * 80)
        print("多Level对比分析 (从Log文件)")
        print("=" * 80)
        
        # 分析每个Level
        level_data_list = []
        level_ids = [level_id1, level_id2, level_id3]
        
        for i, level_id in enumerate(level_ids, 1):
            print(f"\n分析 Level {i}: {level_id}")
            
            # 如果提供了trajectory基础目录，尝试找到对应的trajectory目录
            traj_dir = None
            if traj_base_dir:
                potential_traj_dir = os.path.join(traj_base_dir, level_id)
                if os.path.isdir(potential_traj_dir):
                    traj_dir = potential_traj_dir
                    print(f"  找到trajectory目录: {traj_dir}")
            
            chart_data = analyze_level_from_log(log_file, level_id, traj_dir)
            if chart_data:
                level_data_list.append(chart_data)
                print(f"  找到 {chart_data['task_count']} 个任务")
                print(f"  平均任务总耗时: {chart_data['total_time']:.2f}秒")
                print(f"  平均LLM调用次数: {chart_data['llm_count']} 次")
                print(f"  平均工具调用次数: {chart_data['tool_count']} 次")
            else:
                print(f"  警告: Level {i} 分析失败，跳过")
        
        if len(level_data_list) == 3:
            if output_path is None:
                # 使用第一个level_id作为输出文件名
                output_path = f'multi_level_timing_analysis_{level_id1}.png'
            plot_multi_level_analysis(level_data_list, output_path)
        else:
            print("错误: 需要成功分析3个Level才能生成对比图")
            sys.exit(1)
    
    # 检查是否是3个目录模式
    elif len(args) >= 3:
        # 3个Level对比模式
        dir1 = args[0]
        dir2 = args[1]
        dir3 = args[2]
        output_path = args[3] if len(args) > 3 else None
        
        if not all(os.path.isdir(d) for d in [dir1, dir2, dir3]):
            print("错误: 3个Level模式需要提供3个有效的目录路径")
            sys.exit(1)
        
        print("=" * 80)
        print("多Level对比分析")
        print("=" * 80)
        
        # 分析每个目录
        level_data_list = []
        for i, directory in enumerate([dir1, dir2, dir3], 1):
            print(f"\n分析 Level {i}: {directory}")
            chart_data = analyze_directory(directory, generate_plot=False)
            if chart_data:
                level_data_list.append(chart_data)
            else:
                print(f"警告: Level {i} 分析失败，跳过")
        
        if len(level_data_list) == 3:
            if output_path is None:
                # 使用第一个目录作为输出目录
                output_path = os.path.join(dir1, 'multi_level_timing_analysis.png')
            plot_multi_level_analysis(level_data_list, output_path)
        else:
            print("错误: 需要成功分析3个Level才能生成对比图")
            sys.exit(1)
    else:
        # 单个文件或目录模式
        path = args[0]
        
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

