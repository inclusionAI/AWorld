#!/usr/bin/env python3
"""
日志分析脚本 - 提取性能指标并计算平均耗时占比
"""

import re
import sys
from typing import List, Dict, Tuple
from dataclasses import dataclass
import statistics

@dataclass
class PerformanceMetrics:
    """性能指标数据类"""
    time_per_step: float
    update_actor: float
    gen: float
    
    def calculate_ratios(self) -> Dict[str, float]:
        """计算耗时占比"""
        if self.time_per_step == 0:
            return {"update_actor_ratio": 0.0, "gen_ratio": 0.0}
        
        return {
            "update_actor_ratio": self.update_actor / self.time_per_step,
            "gen_ratio": self.gen / self.time_per_step
        }

class LogAnalyzer:
    """日志分析器"""
    
    def __init__(self):
        # 正则表达式模式，用于提取关键指标
        self.patterns = {
            'time_per_step': r'perf/time_per_step:([\d.]+)',
            'update_actor': r'timing_s/update_actor:([\d.]+)',
            'gen': r'timing_s/gen:([\d.]+)'
        }
    
    def parse_log_line(self, line: str) -> PerformanceMetrics:
        """解析单行日志，提取性能指标"""
        metrics = {}
        
        for key, pattern in self.patterns.items():
            match = re.search(pattern, line)
            if match:
                metrics[key] = float(match.group(1))
            else:
                print(f"警告: 在行中未找到 {key} 指标")
                metrics[key] = 0.0
        
        return PerformanceMetrics(
            time_per_step=metrics['time_per_step'],
            update_actor=metrics['update_actor'],
            gen=metrics['gen']
        )
    
    def analyze_log_file(self, file_path: str) -> List[PerformanceMetrics]:
        """分析日志文件，返回所有性能指标"""
        metrics_list = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    
                    # 检查是否包含关键指标（避免处理无关日志）
                    if 'perf/time_per_step:' in line and 'timing_s/update_actor:' in line and 'timing_s/gen:' in line:
                        try:
                            metrics = self.parse_log_line(line)
                            metrics_list.append(metrics)
                            print(f"第 {line_num} 行: 提取到指标 - time_per_step: {metrics.time_per_step:.2f}s, "
                                  f"update_actor: {metrics.update_actor:.2f}s, gen: {metrics.gen:.2f}s")
                        except Exception as e:
                            print(f"第 {line_num} 行解析失败: {e}")
                            continue
        
        except FileNotFoundError:
            print(f"错误: 文件 {file_path} 不存在")
            return []
        except Exception as e:
            print(f"读取文件时出错: {e}")
            return []
        
        return metrics_list
    
    def calculate_statistics(self, metrics_list: List[PerformanceMetrics]) -> Dict:
        """计算统计信息"""
        if not metrics_list:
            return {"error": "没有找到有效的性能指标"}
        
        # 提取各项指标
        time_per_step_values = [m.time_per_step for m in metrics_list]
        update_actor_values = [m.update_actor for m in metrics_list]
        gen_values = [m.gen for m in metrics_list]
        
        # 计算平均耗时占比
        ratios = [m.calculate_ratios() for m in metrics_list]
        update_actor_ratios = [r["update_actor_ratio"] for r in ratios]
        gen_ratios = [r["gen_ratio"] for r in ratios]
        
        stats = {
            "总记录数": len(metrics_list),
            "time_per_step": {
                "平均值": statistics.mean(time_per_step_values),
                "最大值": max(time_per_step_values),
                "最小值": min(time_per_step_values),
                "标准差": statistics.stdev(time_per_step_values) if len(time_per_step_values) > 1 else 0
            },
            "update_actor": {
                "平均值": statistics.mean(update_actor_values),
                "最大值": max(update_actor_values),
                "最小值": min(update_actor_values),
                "标准差": statistics.stdev(update_actor_values) if len(update_actor_values) > 1 else 0
            },
            "gen": {
                "平均值": statistics.mean(gen_values),
                "最大值": max(gen_values),
                "最小值": min(gen_values),
                "标准差": statistics.stdev(gen_values) if len(gen_values) > 1 else 0
            },
            "平均耗时占比": {
                "update_actor占比": statistics.mean(update_actor_ratios) * 100,
                "gen占比": statistics.mean(gen_ratios) * 100,
                "其他占比": (1 - statistics.mean(update_actor_ratios) - statistics.mean(gen_ratios)) * 100
            }
        }
        
        return stats
    
    def print_results(self, stats: Dict):
        """打印分析结果"""
        if "error" in stats:
            print(f"错误: {stats['error']}")
            return
        
        print("\n" + "="*60)
        print("性能分析结果")
        print("="*60)
        
        print(f"\n总记录数: {stats['总记录数']}")
        
        print(f"\n每步总耗时 (time_per_step):")
        print(f"  平均值: {stats['time_per_step']['平均值']:.2f}s")
        print(f"  最大值: {stats['time_per_step']['最大值']:.2f}s")
        print(f"  最小值: {stats['time_per_step']['最小值']:.2f}s")
        print(f"  标准差: {stats['time_per_step']['标准差']:.2f}s")
        
        print(f"\nActor更新耗时 (update_actor):")
        print(f"  平均值: {stats['update_actor']['平均值']:.2f}s")
        print(f"  最大值: {stats['update_actor']['最大值']:.2f}s")
        print(f"  最小值: {stats['update_actor']['最小值']:.2f}s")
        print(f"  标准差: {stats['update_actor']['标准差']:.2f}s")
        
        print(f"\n生成耗时 (gen):")
        print(f"  平均值: {stats['gen']['平均值']:.2f}s")
        print(f"  最大值: {stats['gen']['最大值']:.2f}s")
        print(f"  最小值: {stats['gen']['最小值']:.2f}s")
        print(f"  标准差: {stats['gen']['标准差']:.2f}s")
        
        print(f"\n平均耗时占比:")
        print(f"  Actor更新占比: {stats['平均耗时占比']['update_actor占比']:.1f}%")
        print(f"  生成占比: {stats['平均耗时占比']['gen占比']:.1f}%")
        print(f"  其他占比: {stats['平均耗时占比']['其他占比']:.1f}%")
        
        print("\n" + "="*60)

def main():
    """主函数"""
    if len(sys.argv) != 2:
        print("用法: python log_analyzer.py <日志文件路径>")
        print("示例: python log_analyzer.py /path/to/your/logfile.log")
        sys.exit(1)
    
    log_file = sys.argv[1]
    analyzer = LogAnalyzer()
    
    print(f"开始分析日志文件: {log_file}")
    print("-" * 60)
    
    # 分析日志文件
    metrics_list = analyzer.analyze_log_file(log_file)
    
    if not metrics_list:
        print("未找到有效的性能指标数据")
        sys.exit(1)
    
    # 计算统计信息
    stats = analyzer.calculate_statistics(metrics_list)
    
    # 打印结果
    analyzer.print_results(stats)

if __name__ == "__main__":
    main()
