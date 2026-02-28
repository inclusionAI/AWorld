#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志解析器 - 从amnicontext_prompt.log中根据Context ID获取最后一条task_id记录
支持并发处理和批次优化
"""

import argparse
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/context_log_parser.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class ContextRecord:
    """Context记录数据结构"""
    context_id: str
    agent_id: str
    task_id: str
    task_input: str
    user_id: str
    session_id: str
    execution_time: str
    timestamp: str
    content: str
    line_number: int


class LogParser:
    """日志解析器类 - 支持并发和批次处理"""
    
    def __init__(self, log_file_path: str, max_workers: int = 4, chunk_size: int = 10000):
        """
        初始化日志解析器
        
        Args:
            log_file_path: 日志文件路径
            max_workers: 最大并发工作线程数
            chunk_size: 文件分块大小（行数）
        """
        self.log_file_path = Path(log_file_path)
        self.max_workers = max_workers
        self.chunk_size = chunk_size
        self.context_pattern = re.compile(
            r'│\s*🤖\s*Context ID:\s*([^│]+)',
            re.MULTILINE
        )
        self.agent_pattern = re.compile(
            r'│\s*🤖\s*Agent ID:\s*([^│]+)',
            re.MULTILINE
        )
        self.task_pattern = re.compile(
            r'│\s*📋\s*Task ID:\s*([^│]+)',
            re.MULTILINE
        )
        self.input_pattern = re.compile(
            r'│\s*📝\s*Task Input:\s*([^│]+)',
            re.MULTILINE
        )
        self.user_pattern = re.compile(
            r'│\s*👨🏻\s*User ID:\s*([^│]+)',
            re.MULTILINE
        )
        self.session_pattern = re.compile(
            r'│\s*💬\s*Session ID:\s*([^│]+)',
            re.MULTILINE
        )
        self.time_pattern = re.compile(
            r'│\s*⏱️\s*Execution Time:\s*([^│]+)',
            re.MULTILINE
        )
        
    def read_file_chunks(self) -> List[List[str]]:
        """
        读取文件并分块处理
        
        Returns:
            文件内容分块列表
        """
        logger.info(f"🚀 开始读取日志文件: {self.log_file_path}")
        
        try:
            with open(self.log_file_path, 'r', encoding='utf-8') as file:
                lines = []
                chunks = []
                
                for line in file:
                    lines.append(line.strip())
                    
                    # 当达到分块大小时，处理当前块
                    if len(lines) >= self.chunk_size:
                        chunks.append(lines.copy())
                        lines.clear()
                        logger.debug(f"📦 处理文件块，当前块数: {len(chunks)}")
                
                # 处理最后一块
                if lines:
                    chunks.append(lines)
                    logger.debug(f"📦 处理最后文件块，总块数: {len(chunks)}")
                
                logger.info(f"✅ 文件读取完成，共分 {len(chunks)} 个块")
                return chunks
                
        except Exception as e:
            logger.error(f"❌ 读取文件失败: {e}")
            raise
    
    def parse_context_record(self, lines: List[str], start_idx: int) -> Optional[ContextRecord]:
        """
        解析单个Context记录 - 从Context ID开始到Execution Time结束
        
        Args:
            lines: 文件行列表
            start_idx: 开始索引
            
        Returns:
            ContextRecord对象或None
        """
        try:
            # 查找Context ID行
            context_line = None
            context_start_idx = None
            for i in range(start_idx, min(start_idx + 20, len(lines))):
                if '🤖 Context ID:' in lines[i]:
                    context_line = lines[i]
                    context_start_idx = i
                    break
            
            if not context_line or context_start_idx is None:
                return None
            
            # 提取Context ID
            context_match = self.context_pattern.search(context_line)
            if not context_match:
                return None
            
            context_id = context_match.group(1).strip()
            
            # 查找Execution Time作为结束位置
            end_idx = context_start_idx + 1
            found_end = False
            
            # 从Context ID开始查找Execution Time
            for i in range(context_start_idx + 1, len(lines)):
                if '⏱️  Execution Time:' in lines[i]:
                    end_idx = i + 1  # 包含Execution Time这一行
                    found_end = True
                    break
            
            # 如果没有找到Execution Time，查找其他结束标志
            if not found_end:
                for i in range(context_start_idx + 1, len(lines)):
                    # 查找各种可能的结束标志
                    if (lines[i].startswith('╰─') or 
                        lines[i].startswith('╭─') or
                        'PROMPT TEMPLATE PARAMETERS' in lines[i] or
                        '🚀 AGENT EXECUTION START' in lines[i]):
                        end_idx = i
                        found_end = True
                        break
            
            # 如果仍然没有找到结束位置，使用文件末尾
            if not found_end:
                end_idx = len(lines)
            
            # 提取记录内容 - 从Context ID开始到Execution Time结束
            record_lines = lines[context_start_idx:end_idx]
            record_content = '\n'.join(record_lines)
            
            # 解析各个字段
            agent_id = self._extract_field(record_content, self.agent_pattern)
            task_id = self._extract_field(record_content, self.task_pattern)
            task_input = self._extract_field(record_content, self.input_pattern)
            user_id = self._extract_field(record_content, self.user_pattern)
            session_id = self._extract_field(record_content, self.session_pattern)
            execution_time = self._extract_field(record_content, self.time_pattern)
            
            # 提取时间戳
            timestamp_match = re.search(r'(\d{8}_\d{6})', context_id)
            timestamp = timestamp_match.group(1) if timestamp_match else ""
            
            return ContextRecord(
                context_id=context_id,
                agent_id=agent_id or "",
                task_id=task_id or "",
                task_input=task_input or "",
                user_id=user_id or "",
                session_id=session_id or "",
                execution_time=execution_time or "",
                timestamp=timestamp,
                content=record_content,
                line_number=context_start_idx
            )
            
        except Exception as e:
            logger.warning(f"⚠️ 解析记录失败 (行 {start_idx}): {e}")
            return None
    
    def _extract_field(self, content: str, pattern: re.Pattern) -> Optional[str]:
        """提取字段值"""
        match = pattern.search(content)
        return match.group(1).strip() if match else None
    
    def parse_chunk(self, chunk_lines: List[str], chunk_idx: int) -> List[ContextRecord]:
        """
        解析单个文件块
        
        Args:
            chunk_lines: 文件块行列表
            chunk_idx: 块索引
            
        Returns:
            ContextRecord列表
        """
        logger.debug(f"🔍 开始解析第 {chunk_idx + 1} 个文件块")
        
        records = []
        i = 0
        
        while i < len(chunk_lines):
            if '🚀 AGENT EXECUTION START' in chunk_lines[i]:
                record = self.parse_context_record(chunk_lines, i)
                if record:
                    records.append(record)
                    logger.debug(f"✅ 解析到记录: {record.context_id}")
            i += 1
        
        logger.info(f"📊 第 {chunk_idx + 1} 个块解析完成，找到 {len(records)} 条记录")
        return records
    
    def find_context_records(self, search_string: str) -> List[ContextRecord]:
        """
        根据搜索字符串查找Context记录
        
        Args:
            search_string: 搜索字符串（如 "dc569c368f7811f0814e627fc1420302|verify_agent---uuiddc5689uuid"）
            
        Returns:
            匹配的ContextRecord列表
        """
        logger.info(f"🔍 开始搜索: {search_string}")
        
        # 读取文件块
        chunks = self.read_file_chunks()
        
        # 并发解析所有块
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for i, chunk in enumerate(chunks):
                future = executor.submit(self.parse_chunk, chunk, i)
                futures.append(future)
            
            # 等待所有解析任务完成
            chunk_results = []
            for future in futures:
                try:
                    result = future.result()
                    chunk_results.append(result)
                except Exception as e:
                    logger.error(f"❌ 块解析失败: {e}")
                    chunk_results.append([])
        
        # 合并结果
        all_records = []
        for result in chunk_results:
            all_records.extend(result)
        
        logger.info(f"📊 总共解析到 {len(all_records)} 条记录")
        
        # 过滤匹配的记录
        matching_records = []
        for record in all_records:
            if search_string in record.context_id:
                matching_records.append(record)
                logger.info(f"🎯 找到匹配记录: {record.context_id}")
        
        # 按时间戳排序，获取最后一条
        matching_records.sort(key=lambda x: x.timestamp, reverse=True)
        
        logger.info(f"✅ 搜索完成，找到 {len(matching_records)} 条匹配记录")
        return matching_records
    
    def get_complete_context_record(self, context_id: str) -> Optional[ContextRecord]:
        """
        根据完整的Context ID获取完整的记录内容
        
        Args:
            context_id: 完整的Context ID
            
        Returns:
            完整的ContextRecord或None
        """
        try:
            logger.info(f"🔍 开始获取完整Context记录: {context_id}")
            
            # 读取文件块
            chunks = self.read_file_chunks()
            
            # 在所有块中搜索完整的Context ID
            for chunk_idx, chunk in enumerate(chunks):
                for i, line in enumerate(chunk):
                    if context_id in line and '🤖 Context ID:' in line:
                        logger.info(f"🎯 在块 {chunk_idx + 1} 中找到Context ID")
                        
                        # 解析这个记录
                        record = self.parse_context_record(chunk, i)
                        if record:
                            logger.info(f"✅ 成功获取完整记录: {record.context_id}")
                            return record
            
            logger.warning(f"⚠️ 未找到完整Context记录: {context_id}")
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取完整记录失败: {e}")
            return None

    def get_latest_task_record(self, search_string: str) -> Optional[ContextRecord]:
        """
        获取指定搜索字符串的最后一条task_id记录
        
        Args:
            search_string: 搜索字符串
            
        Returns:
            最新的ContextRecord或None
        """
        try:
            # 直接调用同步搜索
            records = self.find_context_records(search_string)
            
            if records:
                latest_record = records[0]  # 已按时间戳降序排列
                logger.info(f"🎯 找到最新记录: {latest_record.context_id}")
                
                # 获取完整的记录内容
                complete_record = self.get_complete_context_record(latest_record.context_id)
                if complete_record:
                    return complete_record
                else:
                    logger.warning(f"⚠️ 无法获取完整记录，返回部分记录")
                    return latest_record
            else:
                logger.warning(f"⚠️ 未找到匹配记录: {search_string}")
                return None
                
        except Exception as e:
            logger.error(f"❌ 获取记录失败: {e}")
            return None
    
    def save_record_to_file(self, record: ContextRecord, output_file: str) -> None:
        """
        保存记录到文件 - 输出为txt格式
        
        Args:
            record: ContextRecord对象
            output_file: 输出文件路径
        """
        try:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 确保文件扩展名为.txt
            if not output_path.suffix.lower() == '.txt':
                output_path = output_path.with_suffix('.txt')
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write("Context记录详情\n")
                f.write("=" * 80 + "\n")
                f.write(f"Context ID: {record.context_id}\n")
                f.write(f"Agent ID: {record.agent_id}\n")
                f.write(f"Task ID: {record.task_id}\n")
                f.write(f"User ID: {record.user_id}\n")
                f.write(f"Session ID: {record.session_id}\n")
                f.write(f"执行时间: {record.execution_time}\n")
                f.write(f"时间戳: {record.timestamp}\n")
                f.write(f"行号: {record.line_number}\n\n")
                f.write("任务输入\n")
                f.write("-" * 40 + "\n")
                f.write(f"{record.task_input}\n\n")
                f.write("完整记录内容\n")
                f.write("-" * 40 + "\n")
                f.write(record.content)
                f.write("\n")
            
            logger.info(f"💾 记录已保存到: {output_path}")
            
        except Exception as e:
            logger.error(f"❌ 保存记录失败: {e}")


def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="从amnicontext_prompt.log中根据Context ID获取最后一条task_id记录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python context_log.py -s "dc569c368f7811f0814e627fc1420302|verify_agent---uuiddc5689uuid"
  python context_log.py -s "verify_agent" --show-content
  python context_log.py -s "task_20250910113441" -o "custom_output.md"
  python context_log.py -s "execution_search_agent" -l "custom_log.log" -w 8 -v
        """
    )
    
    parser.add_argument(
        "-s", "--search-string",
        required=True,
        help="搜索字符串，用于匹配Context ID中的内容"
    )
    
    parser.add_argument(
        "-o", "--output-file",
        help="输出文件路径 (默认: results/{search_string}.txt)"
    )
    
    parser.add_argument(
        "-l", "--log-file",
        default="logs/amnicontext_prompt.log",
        help="日志文件路径 (默认: logs/amnicontext_prompt.log)"
    )
    
    parser.add_argument(
        "-w", "--max-workers",
        type=int,
        default=4,
        help="最大并发工作线程数 (默认: 4)"
    )
    
    parser.add_argument(
        "-c", "--chunk-size",
        type=int,
        default=10000,
        help="文件分块大小，行数 (默认: 10000)"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="启用详细日志输出"
    )
    
    parser.add_argument(
        "--show-content",
        action="store_true",
        help="显示完整的任务输入内容"
    )
    
    return parser.parse_args()


def main():
    """主函数 - 支持命令行参数"""
    # 解析命令行参数
    args = parse_arguments()
    
    # 设置默认输出文件名
    if not args.output_file:
        # 清理搜索字符串，移除特殊字符，用作文件名
        safe_filename = re.sub(r'[^\w\-_\.]', '_', args.search_string)
        args.output_file = f"logs/results/{safe_filename}.txt"
    
    # 设置日志级别
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # 检查日志文件是否存在
    if not Path(args.log_file).exists():
        print(f"❌ 日志文件不存在: {args.log_file}")
        sys.exit(1)
    
    # 创建解析器
    parser = LogParser(
        args.log_file,
        max_workers=args.max_workers,
        chunk_size=args.chunk_size
    )
    
    # 查找记录
    logger.info(f"🚀 开始查找Context记录...")
    logger.info(f"🔍 搜索字符串: {args.search_string}")
    logger.info(f"📁 日志文件: {args.log_file}")
    logger.info(f"💾 输出文件: {args.output_file}")
    
    record = parser.get_latest_task_record(args.search_string)
    
    if record:
        print(f"\n🎯 找到最新记录:")
        print(f"  Context ID: {record.context_id}")
        print(f"  Task ID: {record.task_id}")
        print(f"  Agent ID: {record.agent_id}")
        print(f"  User ID: {record.user_id}")
        print(f"  Session ID: {record.session_id}")
        print(f"  执行时间: {record.execution_time}")
        print(f"  时间戳: {record.timestamp}")
        print(f"  行号: {record.line_number}")
        
        # 保存到文件
        parser.save_record_to_file(record, args.output_file)
        print(f"\n💾 记录已保存到: {args.output_file}")
        
        # 显示任务输入内容（如果指定）
        if args.show_content:
            print(f"\n📝 任务输入内容:")
            print("-" * 80)
            print(record.task_input)
            print("-" * 80)
        
        print(f"\n✅ 操作完成！")
        
    else:
        print(f"❌ 未找到匹配的记录: {args.search_string}")
        sys.exit(1)


if __name__ == "__main__":
    main()
