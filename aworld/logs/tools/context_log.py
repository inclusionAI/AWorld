#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Log parser: fetches the latest task_id record by Context ID from amnicontext_prompt.log.
Supports concurrent processing and batch optimization.
"""

import argparse
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Configure logging
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
    """Context record data structure."""
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
    """Log parser with concurrent and batch processing support."""

    def __init__(self, log_file_path: str, max_workers: int = 4, chunk_size: int = 10000):
        """
        Initialize the log parser.

        Args:
            log_file_path: Path to the log file.
            max_workers: Max number of concurrent worker threads.
            chunk_size: File chunk size (number of lines).
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
        Read file and split into chunks.

        Returns:
            List of file content chunks.
        """
        logger.info(f"🚀 Start reading log file: {self.log_file_path}")

        try:
            with open(self.log_file_path, 'r', encoding='utf-8') as file:
                lines = []
                chunks = []

                for line in file:
                    lines.append(line.strip())

                    # When chunk size is reached, process current chunk
                    if len(lines) >= self.chunk_size:
                        chunks.append(lines.copy())
                        lines.clear()
                        logger.debug(f"📦 Processing file chunk, current chunk count: {len(chunks)}")

                # Process remaining lines
                if lines:
                    chunks.append(lines)
                    logger.debug(f"📦 Processing last file chunk, total chunks: {len(chunks)}")

                logger.info(f"✅ File read complete, {len(chunks)} chunks")
                return chunks

        except Exception as e:
            logger.error(f"❌ Failed to read file: {e}")
            raise
    
    def parse_context_record(self, lines: List[str], start_idx: int) -> Optional[ContextRecord]:
        """
        Parse a single Context record from Context ID line to Execution Time line.

        Args:
            lines: List of file lines.
            start_idx: Start index.

        Returns:
            ContextRecord instance or None.
        """
        try:
            # Find Context ID line
            context_line = None
            context_start_idx = None
            for i in range(start_idx, min(start_idx + 20, len(lines))):
                if '🤖 Context ID:' in lines[i]:
                    context_line = lines[i]
                    context_start_idx = i
                    break

            if not context_line or context_start_idx is None:
                return None

            # Extract Context ID
            context_match = self.context_pattern.search(context_line)
            if not context_match:
                return None

            context_id = context_match.group(1).strip()

            # Find Execution Time as end position
            end_idx = context_start_idx + 1
            found_end = False

            # Search for Execution Time from Context ID
            for i in range(context_start_idx + 1, len(lines)):
                if '⏱️  Execution Time:' in lines[i]:
                    end_idx = i + 1  # Include Execution Time line
                    found_end = True
                    break

            # If Execution Time not found, look for other end markers
            if not found_end:
                for i in range(context_start_idx + 1, len(lines)):
                    # Look for possible end markers
                    if (lines[i].startswith('╰─') or
                        lines[i].startswith('╭─') or
                        'PROMPT TEMPLATE PARAMETERS' in lines[i] or
                        '🚀 AGENT EXECUTION START' in lines[i]):
                        end_idx = i
                        found_end = True
                        break

            # If still no end position, use end of lines
            if not found_end:
                end_idx = len(lines)

            # Extract record content from Context ID to Execution Time
            record_lines = lines[context_start_idx:end_idx]
            record_content = '\n'.join(record_lines)

            # Parse fields
            agent_id = self._extract_field(record_content, self.agent_pattern)
            task_id = self._extract_field(record_content, self.task_pattern)
            task_input = self._extract_field(record_content, self.input_pattern)
            user_id = self._extract_field(record_content, self.user_pattern)
            session_id = self._extract_field(record_content, self.session_pattern)
            execution_time = self._extract_field(record_content, self.time_pattern)
            
            # Extract timestamp
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
            logger.warning(f"⚠️ Failed to parse record (line {start_idx}): {e}")
            return None

    def _extract_field(self, content: str, pattern: re.Pattern) -> Optional[str]:
        """Extract field value by pattern."""
        match = pattern.search(content)
        return match.group(1).strip() if match else None

    def parse_chunk(self, chunk_lines: List[str], chunk_idx: int) -> List[ContextRecord]:
        """
        Parse a single file chunk.

        Args:
            chunk_lines: List of chunk lines.
            chunk_idx: Chunk index.

        Returns:
            List of ContextRecord.
        """
        logger.debug(f"🔍 Start parsing chunk {chunk_idx + 1}")
        
        records = []
        i = 0
        
        while i < len(chunk_lines):
            if '🚀 AGENT EXECUTION START' in chunk_lines[i]:
                record = self.parse_context_record(chunk_lines, i)
                if record:
                    records.append(record)
                    logger.debug(f"✅ Parsed record: {record.context_id}")
            i += 1

        logger.info(f"📊 Chunk {chunk_idx + 1} parsed, {len(records)} records")
        return records

    def find_context_records(self, search_string: str) -> List[ContextRecord]:
        """
        Find Context records by search string.

        Args:
            search_string: Search string (e.g. "dc569c368f7811f0814e627fc1420302|verify_agent---uuiddc5689uuid").

        Returns:
            List of matching ContextRecord.
        """
        logger.info(f"🔍 Start search: {search_string}")

        # Read file chunks
        chunks = self.read_file_chunks()

        # Parse all chunks concurrently
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for i, chunk in enumerate(chunks):
                future = executor.submit(self.parse_chunk, chunk, i)
                futures.append(future)

            # Wait for all parse tasks to complete
            chunk_results = []
            for future in futures:
                try:
                    result = future.result()
                    chunk_results.append(result)
                except Exception as e:
                    logger.error(f"❌ Chunk parse failed: {e}")
                    chunk_results.append([])

        # Merge results
        all_records = []
        for result in chunk_results:
            all_records.extend(result)

        logger.info(f"📊 Total records parsed: {len(all_records)}")

        # Filter matching records
        matching_records = []
        for record in all_records:
            if search_string in record.context_id:
                matching_records.append(record)
                logger.info(f"🎯 Matched record: {record.context_id}")

        # Sort by timestamp descending, take latest
        matching_records.sort(key=lambda x: x.timestamp, reverse=True)

        logger.info(f"✅ Search complete, {len(matching_records)} matching records")
        return matching_records

    def get_complete_context_record(self, context_id: str) -> Optional[ContextRecord]:
        """
        Get full record content by complete Context ID.

        Args:
            context_id: Full Context ID.

        Returns:
            Full ContextRecord or None.
        """
        try:
            logger.info(f"🔍 Fetching full Context record: {context_id}")

            # Read file chunks
            chunks = self.read_file_chunks()

            # Search for full Context ID in all chunks
            for chunk_idx, chunk in enumerate(chunks):
                for i, line in enumerate(chunk):
                    if context_id in line and '🤖 Context ID:' in line:
                        logger.info(f"🎯 Found Context ID in chunk {chunk_idx + 1}")

                        # Parse this record
                        record = self.parse_context_record(chunk, i)
                        if record:
                            logger.info(f"✅ Got full record: {record.context_id}")
                            return record

            logger.warning(f"⚠️ Full Context record not found: {context_id}")
            return None

        except Exception as e:
            logger.error(f"❌ Failed to get full record: {e}")
            return None

    def get_latest_task_record(self, search_string: str) -> Optional[ContextRecord]:
        """
        Get the latest task_id record for the given search string.

        Args:
            search_string: Search string.

        Returns:
            Latest ContextRecord or None.
        """
        try:
            # Call synchronous search
            records = self.find_context_records(search_string)

            if records:
                latest_record = records[0]  # Already sorted by timestamp descending
                logger.info(f"🎯 Latest record: {latest_record.context_id}")

                # Get full record content
                complete_record = self.get_complete_context_record(latest_record.context_id)
                if complete_record:
                    return complete_record
                else:
                    logger.warning(f"⚠️ Full record unavailable, returning partial record")
                    return latest_record
            else:
                logger.warning(f"⚠️ No matching record: {search_string}")
                return None

        except Exception as e:
            logger.error(f"❌ Failed to get record: {e}")
            return None

    def save_record_to_file(self, record: ContextRecord, output_file: str) -> None:
        """
        Save record to file as plain text.

        Args:
            record: ContextRecord instance.
            output_file: Output file path.
        """
        try:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Ensure .txt extension
            if not output_path.suffix.lower() == '.txt':
                output_path = output_path.with_suffix('.txt')

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write("Context Record Details\n")
                f.write("=" * 80 + "\n")
                f.write(f"Context ID: {record.context_id}\n")
                f.write(f"Agent ID: {record.agent_id}\n")
                f.write(f"Task ID: {record.task_id}\n")
                f.write(f"User ID: {record.user_id}\n")
                f.write(f"Session ID: {record.session_id}\n")
                f.write(f"Execution Time: {record.execution_time}\n")
                f.write(f"Timestamp: {record.timestamp}\n")
                f.write(f"Line Number: {record.line_number}\n\n")
                f.write("Task Input\n")
                f.write("-" * 40 + "\n")
                f.write(f"{record.task_input}\n\n")
                f.write("Full Record Content\n")
                f.write("-" * 40 + "\n")
                f.write(record.content)
                f.write("\n")

            logger.info(f"💾 Record saved to: {output_path}")

        except Exception as e:
            logger.error(f"❌ Failed to save record: {e}")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Get the latest task_id record by Context ID from amnicontext_prompt.log",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage examples:
  python context_log.py -s "dc569c368f7811f0814e627fc1420302|verify_agent---uuiddc5689uuid"
  python context_log.py -s "verify_agent" --show-content
  python context_log.py -s "task_20250910113441" -o "custom_output.md"
  python context_log.py -s "execution_search_agent" -l "custom_log.log" -w 8 -v
        """
    )

    parser.add_argument(
        "-s", "--search-string",
        required=True,
        help="Search string to match in Context ID"
    )

    parser.add_argument(
        "-o", "--output-file",
        help="Output file path (default: results/{search_string}.txt)"
    )

    parser.add_argument(
        "-l", "--log-file",
        default="logs/amnicontext_prompt.log",
        help="Log file path (default: logs/amnicontext_prompt.log)"
    )

    parser.add_argument(
        "-w", "--max-workers",
        type=int,
        default=4,
        help="Max concurrent worker threads (default: 4)"
    )

    parser.add_argument(
        "-c", "--chunk-size",
        type=int,
        default=10000,
        help="File chunk size in lines (default: 10000)"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose log output"
    )

    parser.add_argument(
        "--show-content",
        action="store_true",
        help="Show full task input content"
    )

    return parser.parse_args()


def main():
    """Main entry with command-line argument support."""
    # Parse command-line arguments
    args = parse_arguments()

    # Set default output filename
    if not args.output_file:
        # Sanitize search string for use as filename
        safe_filename = re.sub(r'[^\w\-_\.]', '_', args.search_string)
        args.output_file = f"logs/results/{safe_filename}.txt"

    # Set log level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Check log file exists
    if not Path(args.log_file).exists():
        print(f"❌ Log file not found: {args.log_file}")
        sys.exit(1)

    # Create parser
    parser = LogParser(
        args.log_file,
        max_workers=args.max_workers,
        chunk_size=args.chunk_size
    )

    # Find record
    logger.info(f"🚀 Start finding Context records...")
    logger.info(f"🔍 Search string: {args.search_string}")
    logger.info(f"📁 Log file: {args.log_file}")
    logger.info(f"💾 Output file: {args.output_file}")

    record = parser.get_latest_task_record(args.search_string)

    if record:
        print(f"\n🎯 Latest record found:")
        print(f"  Context ID: {record.context_id}")
        print(f"  Task ID: {record.task_id}")
        print(f"  Agent ID: {record.agent_id}")
        print(f"  User ID: {record.user_id}")
        print(f"  Session ID: {record.session_id}")
        print(f"  Execution Time: {record.execution_time}")
        print(f"  Timestamp: {record.timestamp}")
        print(f"  Line Number: {record.line_number}")

        # Save to file
        parser.save_record_to_file(record, args.output_file)
        print(f"\n💾 Record saved to: {args.output_file}")

        # Show task input if requested
        if args.show_content:
            print(f"\n📝 Task input content:")
            print("-" * 80)
            print(record.task_input)
            print("-" * 80)

        print(f"\n✅ Done.")

    else:
        print(f"❌ No matching record: {args.search_string}")
        sys.exit(1)


if __name__ == "__main__":
    main()
