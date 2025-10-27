#!/usr/bin/env python3
"""
Log processing script
Used to process log files containing agent_loop related information

Functions:
1. Filter target log lines with "gaia_reward_function|question_scorer="
2. Extract task_id and question_scorer
3. Extract 200 log lines starting from the matched line
4. Extract ground_truth, comp_answer and solution_str fields from these 200 lines
5. Filter related log lines by task_id with "agent_loop|######## task {task_id}"
6. Write results to new file
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
        """Read log file"""
        try:
            with open(self.input_file, 'r', encoding='utf-8') as f:
                return f.readlines()
        except FileNotFoundError:
            print(f"Error: File not found {self.input_file}")
            return []
        except Exception as e:
            print(f"Error reading file: {e}")
            return []
    
    def filter_reward_score_lines(self, lines: List[str]) -> List[str]:
        return lines
        """Step 1: Filter log lines with 'gaia_reward_function|question_scorer='"""
        pattern = r'gaia_reward_function\|question_scorer='
        filtered_lines = []
        
        for line in lines:
            if re.search(pattern, line):
                filtered_lines.append(line.strip())
        
        print(f"Found {len(filtered_lines)} lines containing 'gaia_reward_function|question_scorer='")
        return filtered_lines
    
    def extract_task_info(self, lines: List[str]) -> List[Dict]:
        """Step 2: Extract task_id and question_scorer, and extract 200 subsequent lines for more information"""
        task_info_list = []
        
        for i, line in enumerate(lines):
            # Extract task_id - from ext_info dictionary
            task_id_match = re.search(r"'task_id':\s*'([^']+)'", line)
            # Extract question_scorer - from gaia_reward_function|question_scorer=
            question_scorer_match = re.search(r"gaia_reward_function\|question_scorer=(True|False)", line)
            
            if task_id_match and question_scorer_match:
                task_id = task_id_match.group(1)
                question_scorer = question_scorer_match.group(1)
                
                # Extract 200 subsequent log lines
                subsequent_lines = lines[i:i+500]
                
                # # Remove content between 2nd and 9th | in subsequent_lines
                # cleaned_lines = self.remove_pipe_content_from_lines(subsequent_lines, 2, 9)
                cleaned_lines=subsequent_lines
                
                # Extract ground_truth, comp_answer, solution_str from cleaned lines
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
                print(f"Extracted task_id: {task_id}, question_scorer: {question_scorer}")
                print(f"  ground_truth: {ground_truth[:100] if ground_truth else 'None'}...")
                print(f"  comp_answer: {comp_answer[:100] if comp_answer else 'None'}...")
                print(f"  solution_str: {solution_str[:100] if solution_str else 'None'}...")
        
        return task_info_list
    
    def extract_ground_truth(self, lines: List[str]) -> str:
        """Extract ground_truth from log lines"""
        for line in lines:
            cleaned_line = self.clean_log_line(line)
            match = re.search(r'\|ground_truth=([^|]+)', cleaned_line)
            if match:
                content = match.group(1).strip()
                return content
        return ""
    
    def extract_comp_answer(self, lines: List[str]) -> str:
        """Extract comp_answer from log lines"""
        for line in lines:
            cleaned_line = self.clean_log_line(line)
            match = re.search(r'\|comp_answer=([^|]+)', cleaned_line)
            if match:
                content = match.group(1).strip()
                return content
        return ""
    
    def extract_solution_str(self, lines: List[str]) -> str:
        """Extract solution_str from log lines (multi-line), collect until encountering |ground_truth="""
        solution_lines = []
        in_solution = False
        
        for line in lines:
            # Remove redundant text at the beginning of the line (like ^[[36m(RewardManagerWorker pid=204088)^[[0m)
            cleaned_line = self.clean_log_line(line)
            
            # Find the start of solution_str
            if '|solution_str=' in cleaned_line:
                in_solution = True
                # Extract the part after solution_str= in the first line
                match = re.search(r'\|solution_str=(.*)', cleaned_line)
                if match:
                    content = match.group(1).strip()
                    if content:  # Only add if content is not empty
                        solution_lines.append(content)
                continue
            
            # If inside solution_str, continue collecting lines
            if in_solution:
                # Check if ground_truth field is encountered
                if '|ground_truth=' in cleaned_line:
                    # Encountered ground_truth field, stop collecting
                    break
                else:
                    # Continue collecting solution_str content, ensure ANSI sequences are cleaned
                    content = cleaned_line.strip()
                    if content:  # Only add if content is not empty
                        solution_lines.append(content)
        
        return '\n'.join(solution_lines)
    
    def clean_log_line(self, line: str) -> str:
        """Remove redundant text at the beginning of log lines, like ^[[36m(RewardManagerWorker pid=204088)^[[0m"""
        # Remove ANSI color codes and process information
        # Pattern: ^[[numberm(process info)^[[0m
        cleaned = re.sub(r'\(RewardManagerWorker pid=(\d+)\)', '', line)
        
        # Remove ANSI escape sequences
        # Match ^[[numberm and ^[[0m
        cleaned = re.sub(r'\^\[\[[0-9;]*m', '', cleaned)
        
        # Remove other possible ANSI sequences
        cleaned = re.sub(r'\x1b\[[0-9;]*m', '', cleaned)
        
        return cleaned
    
    def clean_log_line_2(self, line: str) -> str:
        """Remove redundant text at the beginning of log lines, like ^[[36m(RewardManagerWorker pid=204088)^[[0m"""
        # Remove ANSI color codes and process information
        # Pattern: ^[[numberm(process info)^[[0m
        cleaned = re.sub(r'\(AgentLoopWorker pid=(\d+)\)', '', line)
        
        # Remove ANSI escape sequences
        # Match ^[[numberm and ^[[0m
        cleaned = re.sub(r'\^\[\[[0-9;]*m', '', cleaned)
        
        # Remove other possible ANSI sequences
        cleaned = re.sub(r'\x1b\[[0-9;]*m', '', cleaned)
        
        return cleaned

    def remove_pipe_content_from_lines(self, lines: List[str], start_pipe: int, end_pipe: int) -> List[str]:
        """Remove content between the start_pipe-th and end_pipe-th | in each line"""
        cleaned_lines = []
        
        for line in lines:
            cleaned_line = self.remove_pipe_content(line, start_pipe, end_pipe)
            cleaned_lines.append(cleaned_line)
        
        return cleaned_lines
    
    def remove_pipe_content(self, content: str, start_pipe: int, end_pipe: int) -> str:
        """Remove content between the start_pipe-th and end_pipe-th |"""
        if not content:
            return content
        
        # Find all | positions
        pipe_positions = []
        for i, char in enumerate(content):
            if char == '|':
                pipe_positions.append(i)
        
        # If there are not enough |, return original content
        if len(pipe_positions) < end_pipe:
            return content
        
        # If start_pipe or end_pipe is out of range, return original content
        if start_pipe < 1 or end_pipe < start_pipe or start_pipe > len(pipe_positions):
            return content
        
        # Convert to 0-based index
        start_idx = start_pipe - 1
        end_idx = end_pipe - 1
        
        # Ensure indices are within valid range
        if start_idx >= len(pipe_positions) or end_idx >= len(pipe_positions):
            return content
        
        # Build new content: keep content before 1st | + content after end_pipe-th |
        start_pos = pipe_positions[start_idx]
        end_pos = pipe_positions[end_idx]
        
        # Keep content before 1st | (if any)
        before_content = content[:pipe_positions[0]] if pipe_positions else ""
        
        # Keep content after end_pipe-th |
        after_content = content[end_pos + 1:] if end_pos + 1 < len(content) else ""
        
        return before_content + after_content
    
    def filter_task_logs(self, lines: List[str], task_info_list: List[Dict]) -> Dict[str, List[str]]:
        """Step 3: Filter related log lines by task_id with 'agent_loop|######## task {task_id}'"""
        task_logs = {}
        print('Starting log filtering')
        for task_info in task_info_list:
            task_id = task_info['task_id']
            # Build matching patterns - support multiple possible formats
            patterns = [
                rf'agent_loop\|######## task {re.escape(task_id)}',
            ]
            
            matching_lines = []
            for i, line in enumerate(lines):
                for pattern in patterns:
                    if re.search(pattern, line):
                        # Found matching line, start collecting subsequent lines until encountering |process_id
                        current_lines = [line.strip()]
                        
                        # Continue searching forward until encountering |process_id
                        for j in range(i + 1, len(lines)):
                            next_line = lines[j]
                            # Check if |process_id is encountered
                            if '|process_id' in next_line:
                                break
                            # Remove AgentLoopWorker prefix
                            cleaned_line = self.clean_log_line_2(next_line)
                            current_lines.append(cleaned_line.strip())
                        
                        # Join collected lines into a single string
                        combined_content = '\n'.join(current_lines)
                        matching_lines.append(combined_content)
                        break  # Avoid adding the same line repeatedly
            
            task_logs[task_id] = matching_lines
            print(f"Found {len(matching_lines)} related log lines for task_id {task_id}")

        return task_logs
    
    def write_results(self, task_info_list: List[Dict], task_logs: Dict[str, List[str]]):
        """Step 4: Write results to new file"""
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
        
        # Write results in JSON format
        try:
            with open(self.output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"Results written to file: {self.output_file}")
        except Exception as e:
            print(f"Error writing file: {e}")
        
        # Also write in readable text format
        text_output_file = self.output_file.replace('.json', '_readable.txt')
        try:
            with open(text_output_file, 'w', encoding='utf-8') as f:
                for i, result in enumerate(results, 1):
                    # print (result['solution_str'])
                    # lmdflmvldfmv
                    f.write(f"=== Task {i} ===\n")
                    f.write(f"Task ID: {result['task_id']}\n")
                    f.write(f"Question Scorer: {result['question_scorer']}\n")
                    f.write(f"Ground Truth: {result['ground_truth']}\n")
                    f.write(f"Comp Answer: {result['comp_answer']}\n")
                    f.write(f"Solution Str:\n{result['solution_str']}\n")
                    # f.write(f"Original reward line: {result['original_reward_line']}\n")
                    f.write(f"Related log lines count: {len(result['related_logs'])}\n")
                    f.write("Related logs:\n")
                    for log_line in result['related_logs']:
                        f.write(f"  {log_line}\n")
                    f.write("\n" + "="*50 + "\n\n")
            print(f"Readable format results written to file: {text_output_file}")
        except Exception as e:
            print(f"Error writing readable file: {e}")
    
    def process(self):
        """Main processing flow"""
        print(f"Starting to process log file: {self.input_file}")
        
        # Read log file
        lines = self.read_log_file()
        if not lines:
            return
        
        print(f"Total read {len(lines)} log lines")
        
        # Step 1: Filter reward_score lines
        reward_lines = self.filter_reward_score_lines(lines)
        if not reward_lines:
            print("No log lines containing 'agent_loop|reward_score|manager=' found")
            return
        
        # Step 2: Extract task_id and question_scorer
        task_info_list = self.extract_task_info(reward_lines)
        if not task_info_list:
            print("No valid task_id information found")
            return
        
        # Step 3: Filter related log lines
        task_logs = self.filter_task_logs(lines, task_info_list)
        
        # Step 4: Write results
        self.write_results(task_info_list, task_logs)
        
        print("Processing completed!")


def main():
    parser = argparse.ArgumentParser(description='Process log files containing agent_loop information')
    parser.add_argument('input_file', help='Input log file path')
    parser.add_argument('-o', '--output', default='processed_logs.json', help='Output file path (default: processed_logs.json)')
    
    args = parser.parse_args()
    
    # Check if input file exists
    if not Path(args.input_file).exists():
        print(f"Error: Input file {args.input_file} does not exist")
        return
    
    # Create processor and run
    processor = LogProcessor(args.input_file, args.output)
    processor.process()


if __name__ == "__main__":
    main()