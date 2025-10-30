#!/usr/bin/env python3
"""
Log analysis script - Extract performance metrics and calculate average time consumption ratios
"""

import re
import sys
from typing import List, Dict, Tuple
from dataclasses import dataclass
import statistics

@dataclass
class PerformanceMetrics:
    """Performance metrics data class"""
    time_per_step: float
    update_actor: float
    gen: float
    
    def calculate_ratios(self) -> Dict[str, float]:
        """Calculate time consumption ratios"""
        if self.time_per_step == 0:
            return {"update_actor_ratio": 0.0, "gen_ratio": 0.0}
        
        return {
            "update_actor_ratio": self.update_actor / self.time_per_step,
            "gen_ratio": self.gen / self.time_per_step
        }

class LogAnalyzer:
    """Log analyzer"""
    
    def __init__(self):
        # Regular expression patterns for extracting key metrics
        self.patterns = {
            'time_per_step': r'perf/time_per_step:([\d.]+)',
            'update_actor': r'timing_s/update_actor:([\d.]+)',
            'gen': r'timing_s/gen:([\d.]+)'
        }
    
    def parse_log_line(self, line: str) -> PerformanceMetrics:
        """Parse a single log line and extract performance metrics"""
        metrics = {}
        
        for key, pattern in self.patterns.items():
            match = re.search(pattern, line)
            if match:
                metrics[key] = float(match.group(1))
            else:
                print(f"Warning: {key} metric not found in line")
                metrics[key] = 0.0
        
        return PerformanceMetrics(
            time_per_step=metrics['time_per_step'],
            update_actor=metrics['update_actor'],
            gen=metrics['gen']
        )
    
    def analyze_log_file(self, file_path: str) -> List[PerformanceMetrics]:
        """Analyze log file and return all performance metrics"""
        metrics_list = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    
                    # Check if line contains key metrics (avoid processing irrelevant logs)
                    if 'perf/time_per_step:' in line and 'timing_s/update_actor:' in line and 'timing_s/gen:' in line:
                        try:
                            metrics = self.parse_log_line(line)
                            metrics_list.append(metrics)
                            print(f"Line {line_num}: Extracted metrics - time_per_step: {metrics.time_per_step:.2f}s, "
                                  f"update_actor: {metrics.update_actor:.2f}s, gen: {metrics.gen:.2f}s")
                        except Exception as e:
                            print(f"Line {line_num} parsing failed: {e}")
                            continue
        
        except FileNotFoundError:
            print(f"Error: File {file_path} not found")
            return []
        except Exception as e:
            print(f"Error reading file: {e}")
            return []
        
        return metrics_list
    
    def calculate_statistics(self, metrics_list: List[PerformanceMetrics]) -> Dict:
        """Calculate statistics"""
        if not metrics_list:
            return {"error": "No valid performance metrics found"}
        
        # Extract each metric
        time_per_step_values = [m.time_per_step for m in metrics_list]
        update_actor_values = [m.update_actor for m in metrics_list]
        gen_values = [m.gen for m in metrics_list]
        
        # Calculate average time consumption ratios
        ratios = [m.calculate_ratios() for m in metrics_list]
        update_actor_ratios = [r["update_actor_ratio"] for r in ratios]
        gen_ratios = [r["gen_ratio"] for r in ratios]
        
        stats = {
            "Total records": len(metrics_list),
            "time_per_step": {
                "Average": statistics.mean(time_per_step_values),
                "Maximum": max(time_per_step_values),
                "Minimum": min(time_per_step_values),
                "Standard deviation": statistics.stdev(time_per_step_values) if len(time_per_step_values) > 1 else 0
            },
            "update_actor": {
                "Average": statistics.mean(update_actor_values),
                "Maximum": max(update_actor_values),
                "Minimum": min(update_actor_values),
                "Standard deviation": statistics.stdev(update_actor_values) if len(update_actor_values) > 1 else 0
            },
            "gen": {
                "Average": statistics.mean(gen_values),
                "Maximum": max(gen_values),
                "Minimum": min(gen_values),
                "Standard deviation": statistics.stdev(gen_values) if len(gen_values) > 1 else 0
            },
            "Average time consumption ratios": {
                "update_actor ratio": statistics.mean(update_actor_ratios) * 100,
                "gen ratio": statistics.mean(gen_ratios) * 100,
                "Other ratio": (1 - statistics.mean(update_actor_ratios) - statistics.mean(gen_ratios)) * 100
            }
        }
        
        return stats
    
    def print_results(self, stats: Dict):
        """Print analysis results"""
        if "error" in stats:
            print(f"Error: {stats['error']}")
            return
        
        print("\n" + "="*60)
        print("Performance Analysis Results")
        print("="*60)
        
        print(f"\nTotal records: {stats['Total records']}")
        
        print(f"\nTotal time per step (time_per_step):")
        print(f"  Average: {stats['time_per_step']['Average']:.2f}s")
        print(f"  Maximum: {stats['time_per_step']['Maximum']:.2f}s")
        print(f"  Minimum: {stats['time_per_step']['Minimum']:.2f}s")
        print(f"  Standard deviation: {stats['time_per_step']['Standard deviation']:.2f}s")
        
        print(f"\nActor update time (update_actor):")
        print(f"  Average: {stats['update_actor']['Average']:.2f}s")
        print(f"  Maximum: {stats['update_actor']['Maximum']:.2f}s")
        print(f"  Minimum: {stats['update_actor']['Minimum']:.2f}s")
        print(f"  Standard deviation: {stats['update_actor']['Standard deviation']:.2f}s")
        
        print(f"\nGeneration time (gen):")
        print(f"  Average: {stats['gen']['Average']:.2f}s")
        print(f"  Maximum: {stats['gen']['Maximum']:.2f}s")
        print(f"  Minimum: {stats['gen']['Minimum']:.2f}s")
        print(f"  Standard deviation: {stats['gen']['Standard deviation']:.2f}s")
        
        print(f"\nAverage time consumption ratios:")
        print(f"  Actor update ratio: {stats['Average time consumption ratios']['update_actor ratio']:.1f}%")
        print(f"  Generation ratio: {stats['Average time consumption ratios']['gen ratio']:.1f}%")
        print(f"  Other ratio: {stats['Average time consumption ratios']['Other ratio']:.1f}%")
        
        print("\n" + "="*60)

def main():
    """Main function"""
    if len(sys.argv) != 2:
        print("Usage: python log_analyzer.py <log_file_path>")
        print("Example: python log_analyzer.py /path/to/your/logfile.log")
        sys.exit(1)
    
    log_file = sys.argv[1]
    analyzer = LogAnalyzer()
    
    print(f"Starting to analyze log file: {log_file}")
    print("-" * 60)
    
    # Analyze log file
    metrics_list = analyzer.analyze_log_file(log_file)
    
    if not metrics_list:
        print("No valid performance metrics data found")
        sys.exit(1)
    
    # Calculate statistics
    stats = analyzer.calculate_statistics(metrics_list)
    
    # Print results
    analyzer.print_results(stats)

if __name__ == "__main__":
    main()