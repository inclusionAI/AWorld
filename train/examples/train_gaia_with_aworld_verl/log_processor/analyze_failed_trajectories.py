#!/usr/bin/env python3
"""
分析trajectory文件中由于网络异常、登录拦截、验证码、加载中等原因无法得到结果的文件
"""

import ast
from pathlib import Path
from typing import List, Dict, Tuple


class TrajectoryAnalyzer:
    """Trajectory分析器"""
    
    def __init__(self):
        # 定义失败原因关键词
        self.failure_keywords = {
            'network': [
                '网络异常', '网络连接', '网络问题', '网络错误', '网络超时',
                '连接失败', '连接超时', '无法连接', '连接问题',
                'ERR_TIMED_OUT', 'net::ERR', 'timeout',
                '网络', '连接', '超时'
            ],
            'login': [
                '登录拦截', '登录失败', '需要登录', '请登录', '登录验证',
                '登录', '拦截', '认证', '身份验证'
            ],
            'captcha': [
                '验证码', 'captcha', '验证', '人机验证', '安全验证',
                '图片验证', '滑动验证'
            ],
            'loading': [
                '加载中', '正在加载', '页面加载', '加载失败', '加载超时',
                '未渲染完毕', '渲染中', '等待加载', '加载问题',
                '页面未加载', '加载缓慢', '加载卡住', '加载异常',
                'loading', 'page loading', 'render', 'rendering'
            ]
        }
    
    def parse_trajectory_file(self, file_path: str) -> Dict:
        """解析trajectory文件（Python字典格式）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 使用ast.literal_eval解析Python字典格式
            try:
                data = ast.literal_eval(content)
                return data
            except (ValueError, SyntaxError) as e:
                print(f"警告: 无法解析文件 {file_path}: {e}")
                return {}
        except Exception as e:
            print(f"错误: 读取文件 {file_path} 失败: {e}")
            return {}
    
    def get_last_message_content(self, trajectory: Dict) -> str:
        """获取trajectory中最后一条message的content"""
        if not trajectory:
            return ""
        
        # 尝试按key排序找到最后一条消息
        # 如果key是数字，按数字排序；否则按字符串排序
        try:
            # 尝试将key转换为数字进行排序
            sorted_items = sorted(
                trajectory.items(),
                key=lambda x: int(x[0]) if str(x[0]).isdigit() else float('inf')
            )
        except (ValueError, TypeError):
            # 如果转换失败，按字符串排序
            sorted_items = sorted(trajectory.items())
        
        # 从后往前查找最后一条有content的消息
        for key, message in reversed(sorted_items):
            if isinstance(message, dict) and 'content' in message:
                content = message.get('content', '')
                if content and isinstance(content, str):
                    return content
        
        return ""
    
    def check_failure_reasons(self, answer_text: str) -> List[str]:
        """检查answer文本中是否包含失败原因"""
        found_reasons = []
        answer_lower = answer_text.lower()
        
        for reason_type, keywords in self.failure_keywords.items():
            for keyword in keywords:
                if keyword.lower() in answer_lower:
                    if reason_type not in found_reasons:
                        found_reasons.append(reason_type)
                    break
        
        return found_reasons
    
    def analyze_file(self, file_path: str) -> Tuple[bool, List[str], str]:
        """
        分析单个文件
        返回: (是否包含失败原因, 失败原因列表, 最后一条消息内容摘要)
        """
        trajectory = self.parse_trajectory_file(file_path)
        if not trajectory:
            return False, [], ""
        
        last_message_content = self.get_last_message_content(trajectory)
        if not last_message_content:
            return False, [], ""
        
        # 检查失败原因
        failure_reasons = self.check_failure_reasons(last_message_content)
        
        # 生成摘要（前200个字符）
        summary = last_message_content[:200].replace('\n', ' ') if last_message_content else ""
        
        return len(failure_reasons) > 0, failure_reasons, summary
    
    def analyze_directory(self, directory_path: str) -> Dict[str, Dict]:
        """分析目录下的所有trajectory文件"""
        results = {}
        directory = Path(directory_path)
        
        if not directory.exists():
            print(f"错误: 目录不存在: {directory_path}")
            return results
        
        # 查找所有traj_*.json文件
        trajectory_files = sorted(directory.glob("traj_*.json"))
        
        print(f"找到 {len(trajectory_files)} 个trajectory文件")
        print("-" * 80)
        
        for file_path in trajectory_files:
            has_failure, reasons, summary = self.analyze_file(str(file_path))
            
            if has_failure:
                results[file_path.name] = {
                    'file_path': str(file_path),
                    'failure_reasons': reasons,
                    'summary': summary
                }
                print(f"✓ {file_path.name}")
                print(f"  失败原因: {', '.join(reasons)}")
                if summary:
                    print(f"  摘要: {summary}...")
                print()
        
        return results
    
    def print_summary(self, results: Dict[str, Dict]):
        """打印分析结果摘要"""
        print("=" * 80)
        print("分析结果摘要")
        print("=" * 80)
        print(f"总共找到 {len(results)} 个包含失败原因的trajectory文件\n")
        
        # 按失败原因分类统计
        reason_stats = {}
        for file_info in results.values():
            for reason in file_info['failure_reasons']:
                reason_stats[reason] = reason_stats.get(reason, 0) + 1
        
        print("失败原因统计:")
        for reason, count in sorted(reason_stats.items(), key=lambda x: x[1], reverse=True):
            reason_name = {
                'network': '网络异常',
                'login': '登录拦截',
                'captcha': '验证码',
                'loading': '加载中'
            }.get(reason, reason)
            print(f"  {reason_name}: {count} 个文件")
        
        print("\n文件列表:")
        for filename in sorted(results.keys()):
            print(f"  - {filename}")


def main():
    """主函数"""
    import sys
    
    if len(sys.argv) != 2:
        print("用法: python analyze_failed_trajectories.py <trajectory_directory>")
        print("示例: python analyze_failed_trajectories.py /path/to/trajectory/directory")
        sys.exit(1)
    
    directory_path = sys.argv[1]
    
    analyzer = TrajectoryAnalyzer()
    results = analyzer.analyze_directory(directory_path)
    
    if results:
        analyzer.print_summary(results)
    else:
        print("未找到包含失败原因的trajectory文件")


if __name__ == "__main__":
    main()

