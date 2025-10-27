#!/usr/bin/env python3
"""
日志处理脚本
用于处理包含agent_loop相关信息的日志文件

功能：
1. 用"gaia_reward_function|question_scorer="过滤目标日志行
2. 提取task_id和question_scorer
3. 从匹配行开始往后截取200行日志
4. 从这200行中提取ground_truth、comp_answer和solution_str字段
5. 用task_id按"agent_loop|######## task {task_id}"过滤相关日志行
6. 将结果写入新文件
"""

import re
import json
import argparse
from typing import List, Dict, Tuple
from pathlib import Path


class LogProcessor:
    def __init__(self, input_file: str, output_file: str):
        self.input_file = input_file
        self.output_file = output_file
        
    def read_log_file(self) -> List[str]:
        """读取日志文件"""
        try:
            with open(self.input_file, 'r', encoding='utf-8') as f:
                return f.readlines()
        except FileNotFoundError:
            print(f"错误：找不到文件 {self.input_file}")
            return []
        except Exception as e:
            print(f"读取文件时出错：{e}")
            return []
    
    def filter_reward_score_lines(self, lines: List[str]) -> List[str]:
        return lines
        """第一步：用'gaia_reward_function|question_scorer='过滤日志行"""
        pattern = r'gaia_reward_function\|question_scorer='
        filtered_lines = []
        
        for line in lines:
            if re.search(pattern, line):
                filtered_lines.append(line.strip())
        
        print(f"找到 {len(filtered_lines)} 行包含 'gaia_reward_function|question_scorer=' 的日志")
        return filtered_lines
    
    def extract_task_info(self, lines: List[str]) -> List[Dict]:
        """第二步：提取task_id和question_scorer，并截取后续200行提取更多信息"""
        task_info_list = []
        
        for i, line in enumerate(lines):
            # 提取task_id - 从ext_info字典中提取
            task_id_match = re.search(r"'task_id':\s*'([^']+)'", line)
            # 提取question_scorer - 从gaia_reward_function|question_scorer=中提取
            question_scorer_match = re.search(r"gaia_reward_function\|question_scorer=(True|False)", line)
            
            if task_id_match and question_scorer_match:
                task_id = task_id_match.group(1)
                question_scorer = question_scorer_match.group(1)
                
                # 截取后续200行日志
                subsequent_lines = lines[i:i+500]
                
                # # 去除subsequent_lines中第2个|到第9个|之间的内容
                # cleaned_lines = self.remove_pipe_content_from_lines(subsequent_lines, 2, 9)
                cleaned_lines=subsequent_lines
                
                # 从清理后的行中提取ground_truth, comp_answer, solution_str
                ground_truth = self.extract_ground_truth(cleaned_lines)
                comp_answer = self.extract_comp_answer(cleaned_lines)
                solution_str = self.extract_solution_str(cleaned_lines)
                
                task_info = {
                    'task_id': task_id,
                    'question_scorer': question_scorer,
                    'ground_truth': ground_truth,
                    'comp_answer': comp_answer,
                    'solution_str': solution_str,
                    'original_line': line,
                    'subsequent_lines': subsequent_lines
                }
                task_info_list.append(task_info)
                print(f"提取到 task_id: {task_id}, question_scorer: {question_scorer}")
                print(f"  ground_truth: {ground_truth[:100] if ground_truth else 'None'}...")
                print(f"  comp_answer: {comp_answer[:100] if comp_answer else 'None'}...")
                print(f"  solution_str: {solution_str[:100] if solution_str else 'None'}...")
        
        return task_info_list
    
    def extract_ground_truth(self, lines: List[str]) -> str:
        """从日志行中提取ground_truth"""
        for line in lines:
            cleaned_line = self.clean_log_line(line)
            match = re.search(r'\|ground_truth=([^|]+)', cleaned_line)
            if match:
                content = match.group(1).strip()
                return content
        return ""
    
    def extract_comp_answer(self, lines: List[str]) -> str:
        """从日志行中提取comp_answer"""
        for line in lines:
            cleaned_line = self.clean_log_line(line)
            match = re.search(r'\|comp_answer=([^|]+)', cleaned_line)
            if match:
                content = match.group(1).strip()
                return content
        return ""
    
    def extract_solution_str(self, lines: List[str]) -> str:
        """从日志行中提取solution_str（多行），一直收集到遇到|ground_truth=为止"""
        solution_lines = []
        in_solution = False
        
        for line in lines:
            # 去除行首的冗余文本（如^[[36m(RewardManagerWorker pid=204088)^[[0m）
            cleaned_line = self.clean_log_line(line)
            
            # 查找solution_str的开始
            if '|solution_str=' in cleaned_line:
                in_solution = True
                # 提取第一行中solution_str=后面的部分
                match = re.search(r'\|solution_str=(.*)', cleaned_line)
                if match:
                    content = match.group(1).strip()
                    if content:  # 只有当内容不为空时才添加
                        solution_lines.append(content)
                continue
            
            # 如果在solution_str内部，继续收集行
            if in_solution:
                # 检查是否遇到ground_truth字段
                if '|ground_truth=' in cleaned_line:
                    # 遇到ground_truth字段，停止收集
                    break
                else:
                    # 继续收集solution_str的内容，确保清理ANSI序列
                    content = cleaned_line.strip()
                    if content:  # 只有当内容不为空时才添加
                        solution_lines.append(content)
        
        return '\n'.join(solution_lines)
    
    def clean_log_line(self, line: str) -> str:
        """去除日志行首的冗余文本，如^[[36m(RewardManagerWorker pid=204088)^[[0m"""
        # 去除ANSI颜色码和进程信息
        # 匹配模式：^[[数字m(进程信息)^[[0m
        cleaned = re.sub(r'\(RewardManagerWorker pid=(\d+)\)', '', line)
        
        # 去除ANSI转义序列
        # 匹配 ^[[数字m 和 ^[[0m
        cleaned = re.sub(r'\^\[\[[0-9;]*m', '', cleaned)
        
        # 去除其他可能的ANSI序列
        cleaned = re.sub(r'\x1b\[[0-9;]*m', '', cleaned)
        
        return cleaned
    
    def clean_log_line_2(self, line: str) -> str:
        """去除日志行首的冗余文本，如^[[36m(RewardManagerWorker pid=204088)^[[0m"""
        # 去除ANSI颜色码和进程信息
        # 匹配模式：^[[数字m(进程信息)^[[0m
        cleaned = re.sub(r'\(AgentLoopWorker pid=(\d+)\)', '', line)
        
        # 去除ANSI转义序列
        # 匹配 ^[[数字m 和 ^[[0m
        cleaned = re.sub(r'\^\[\[[0-9;]*m', '', cleaned)
        
        # 去除其他可能的ANSI序列
        cleaned = re.sub(r'\x1b\[[0-9;]*m', '', cleaned)
        
        return cleaned

    def remove_pipe_content_from_lines(self, lines: List[str], start_pipe: int, end_pipe: int) -> List[str]:
        """去除每行中第start_pipe个|到第end_pipe个|之间的内容"""
        cleaned_lines = []
        
        for line in lines:
            cleaned_line = self.remove_pipe_content(line, start_pipe, end_pipe)
            cleaned_lines.append(cleaned_line)
        
        return cleaned_lines
    
    def remove_pipe_content(self, content: str, start_pipe: int, end_pipe: int) -> str:
        """去除第start_pipe个|到第end_pipe个|之间的内容"""
        if not content:
            return content
        
        # 找到所有|的位置
        pipe_positions = []
        for i, char in enumerate(content):
            if char == '|':
                pipe_positions.append(i)
        
        # 如果|的数量不足，返回原内容
        if len(pipe_positions) < end_pipe:
            return content
        
        # 如果start_pipe或end_pipe超出范围，返回原内容
        if start_pipe < 1 or end_pipe < start_pipe or start_pipe > len(pipe_positions):
            return content
        
        # 转换为0-based索引
        start_idx = start_pipe - 1
        end_idx = end_pipe - 1
        
        # 确保索引在有效范围内
        if start_idx >= len(pipe_positions) or end_idx >= len(pipe_positions):
            return content
        
        # 构建新内容：保留第1个|之前的内容 + 第end_pipe个|之后的内容
        start_pos = pipe_positions[start_idx]
        end_pos = pipe_positions[end_idx]
        
        # 保留第1个|之前的内容（如果有的话）
        before_content = content[:pipe_positions[0]] if pipe_positions else ""
        
        # 保留第end_pipe个|之后的内容
        after_content = content[end_pos + 1:] if end_pos + 1 < len(content) else ""
        
        return before_content + after_content
    
    def filter_task_logs(self, lines: List[str], task_info_list: List[Dict]) -> Dict[str, List[str]]:
        """第三步：用task_id按'agent_loop|######## task {task_id}'过滤相关日志行"""
        task_logs = {}
        print('过滤日志开始')
        for task_info in task_info_list:
            task_id = task_info['task_id']
            # 构建匹配模式 - 支持多种可能的格式
            patterns = [
                rf'agent_loop\|######## task {re.escape(task_id)}',
            ]
            
            matching_lines = []
            for i, line in enumerate(lines):
                for pattern in patterns:
                    if re.search(pattern, line):
                        # 找到匹配行，开始收集后续行直到遇到|process_id为止
                        current_lines = [line.strip()]
                        
                        # 继续往后查找，直到遇到|process_id为止
                        for j in range(i + 1, len(lines)):
                            next_line = lines[j]
                            # 检查是否遇到|process_id
                            if '|process_id' in next_line:
                                break
                            # 移除AgentLoopWorker前缀
                            cleaned_line = self.clean_log_line_2(next_line)
                            current_lines.append(cleaned_line.strip())
                        
                        # 将收集到的行拼接成一个字符串
                        combined_content = '\n'.join(current_lines)
                        matching_lines.append(combined_content)
                        break  # 避免重复添加同一行
            
            task_logs[task_id] = matching_lines
            print(f"为 task_id {task_id} 找到 {len(matching_lines)} 行相关日志")

        return task_logs
    
    def write_results(self, task_info_list: List[Dict], task_logs: Dict[str, List[str]]):
        """第四步：将结果写入新文件"""
        results = []
        
        for task_info in task_info_list:
            task_id = task_info['task_id']
            result = {
                'task_id': task_id,
                'question_scorer': task_info['question_scorer'],
                'ground_truth': task_info.get('ground_truth', ''),
                'comp_answer': task_info.get('comp_answer', ''),
                'solution_str': task_info.get('solution_str', ''),
                'original_reward_line': task_info['original_line'],
                'related_logs': task_logs.get(task_id, [])
            }
            results.append(result)
        
        # 写入JSON格式的结果
        try:
            with open(self.output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"结果已写入文件：{self.output_file}")
        except Exception as e:
            print(f"写入文件时出错：{e}")
        
        # 同时写入可读的文本格式
        text_output_file = self.output_file.replace('.json', '_readable.txt')
        try:
            with open(text_output_file, 'w', encoding='utf-8') as f:
                for i, result in enumerate(results, 1):
                    # print (result['solution_str'])
                    # lmdflmvldfmv
                    f.write(f"=== 任务 {i} ===\n")
                    f.write(f"Task ID: {result['task_id']}\n")
                    f.write(f"Question Scorer: {result['question_scorer']}\n")
                    f.write(f"Ground Truth: {result['ground_truth']}\n")
                    f.write(f"Comp Answer: {result['comp_answer']}\n")
                    f.write(f"Solution Str:\n{result['solution_str']}\n")
                    # f.write(f"原始奖励行: {result['original_reward_line']}\n")
                    f.write(f"相关日志行数: {len(result['related_logs'])}\n")
                    f.write("相关日志:\n")
                    for log_line in result['related_logs']:
                        f.write(f"  {log_line}\n")
                    f.write("\n" + "="*50 + "\n\n")
            print(f"可读格式结果已写入文件：{text_output_file}")
        except Exception as e:
            print(f"写入可读文件时出错：{e}")
    
    def process(self):
        """主处理流程"""
        print(f"开始处理日志文件：{self.input_file}")
        
        # 读取日志文件
        lines = self.read_log_file()
        if not lines:
            return
        
        print(f"总共读取了 {len(lines)} 行日志")
        
        # 第一步：过滤reward_score行
        reward_lines = self.filter_reward_score_lines(lines)
        if not reward_lines:
            print("未找到包含 'agent_loop|reward_score|manager=' 的日志行")
            return
        
        # 第二步：提取task_id和question_scorer
        task_info_list = self.extract_task_info(reward_lines)
        if not task_info_list:
            print("未找到有效的task_id信息")
            return
        
        # 第三步：过滤相关日志行
        task_logs = self.filter_task_logs(lines, task_info_list)
        
        # 第四步：写入结果
        self.write_results(task_info_list, task_logs)
        
        print("处理完成！")


def main():
    parser = argparse.ArgumentParser(description='处理包含agent_loop信息的日志文件')
    parser.add_argument('input_file', help='输入日志文件路径')
    parser.add_argument('-o', '--output', default='processed_logs.json', help='输出文件路径（默认：processed_logs.json）')
    
    args = parser.parse_args()
    
    # 检查输入文件是否存在
    if not Path(args.input_file).exists():
        print(f"错误：输入文件 {args.input_file} 不存在")
        return
    
    # 创建处理器并运行
    processor = LogProcessor(args.input_file, args.output)
    processor.process()


if __name__ == "__main__":
    main()
