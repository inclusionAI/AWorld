"""
Tool call logging system for debugging and AI-assisted problem diagnosis.

This logger creates structured, context-rich logs that are:
1. Human-readable: Engineers can quickly locate issues
2. AI-understandable: Models can read logs to diagnose problems
3. Searchable: Indexed by session, tool name, and timestamp

Log structure:
- ~/.aworld/tool_calls/<session_id>.jsonl - Structured tool call records
- ~/.aworld/tool_calls/latest -> symlink to current session
- ~/.aworld/tool_calls/index.json - Search index
"""
import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import time


class ToolCallRecord:
    """
    A single tool call record with rich context.

    Designed to be both human and AI-readable.
    """

    def __init__(
        self,
        tool_name: str,
        args: Dict[str, Any],
        output: str,
        duration: float,
        status: str = "success",
        error: Optional[str] = None,
        metadata: Optional[Dict] = None,
        context: Optional[Dict] = None
    ):
        self.timestamp = datetime.utcnow().isoformat() + "Z"
        self.tool_name = tool_name
        self.args = args
        self.output = output
        self.output_lines = len(output.split('\n')) if output else 0
        self.output_chars = len(output) if output else 0
        self.duration = duration
        self.status = status
        self.error = error
        self.metadata = metadata or {}
        self.context = context or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "_comment": "AWorld tool call log - AI/human readable",
            "_schema_version": "1.0",
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "args": self.args,
            "output": self.output[:1000] if self.output else None,  # Truncate for main log
            "output_stats": {
                "lines": self.output_lines,
                "chars": self.output_chars,
                "truncated": self.output_chars > 1000
            },
            "duration_seconds": round(self.duration, 3),
            "status": self.status,
            "error": self.error,
            "metadata": self.metadata,
            "context": self.context
        }

    def to_human_readable(self) -> str:
        """
        Format as human-readable text for quick scanning.

        Example:
        [2026-04-03T13:30:00.123Z] ✓ terminal → bash (2.3s)
        Args: command="ls -la | head -20"
        Output: 20 lines, 1.2KB
        """
        status_symbol = "✓" if self.status == "success" else "✗"

        lines = [
            f"[{self.timestamp}] {status_symbol} {self.tool_name} ({self.duration:.2f}s)",
            f"Args: {self._format_args()}",
            f"Output: {self.output_lines} lines, {self._format_size(self.output_chars)}"
        ]

        if self.error:
            lines.append(f"Error: {self.error}")

        if self.metadata.get('summary'):
            lines.append(f"Summary: {self.metadata['summary']}")

        return "\n".join(lines)

    def _format_args(self) -> str:
        """Format args for display."""
        if not self.args:
            return "(none)"

        # Show first 2 args
        items = list(self.args.items())[:2]
        formatted = ", ".join(f"{k}={repr(v)[:50]}" for k, v in items)

        if len(self.args) > 2:
            formatted += f", ... (+{len(self.args) - 2} more)"

        return formatted

    def _format_size(self, chars: int) -> str:
        """Format byte size."""
        if chars < 1024:
            return f"{chars}B"
        elif chars < 1024 * 1024:
            return f"{chars / 1024:.1f}KB"
        else:
            return f"{chars / (1024 * 1024):.1f}MB"


class ToolLogger:
    """
    Tool call logger with rich context for debugging and AI diagnosis.

    Features:
    - Session-based logging
    - Full output preservation for large results
    - Human-readable summaries
    - AI-friendly structured format
    - Search and retrieval
    """

    def __init__(self, log_dir: Optional[str] = None):
        """
        Initialize tool logger.

        Args:
            log_dir: Directory for logs (default: ~/.aworld/tool_calls)
        """
        self.log_dir = Path(log_dir or "~/.aworld/tool_calls").expanduser()
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Create outputs subdirectory for large results
        self.outputs_dir = self.log_dir / "outputs"
        self.outputs_dir.mkdir(exist_ok=True)

        self.session_id = None
        self.session_log_file = None
        self.call_count = 0

    def start_session(self, session_id: str, metadata: Optional[Dict] = None):
        """
        Start logging for a new session.

        Args:
            session_id: Unique session identifier
            metadata: Optional session metadata (agent name, project, etc.)

        Returns:
            Path to session log file
        """
        self.session_id = session_id
        self.session_log_file = self.log_dir / f"{session_id}.jsonl"
        self.call_count = 0

        # Write session header
        header = {
            "_type": "session_start",
            "_comment": "AWorld tool call session log - AI/human readable",
            "session_id": session_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metadata": metadata or {},
            "format_version": "1.0"
        }

        with open(self.session_log_file, 'w', encoding='utf-8') as f:
            f.write(json.dumps(header, ensure_ascii=False, indent=2) + '\n')

        # Update "latest" symlink
        latest_link = self.log_dir / "latest"
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(self.session_log_file)

        return self.session_log_file

    def log_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        output: str,
        duration: float,
        status: str = "success",
        error: Optional[str] = None,
        error_traceback: Optional[str] = None,
        metadata: Optional[Dict] = None,
        context: Optional[Dict] = None
    ) -> ToolCallRecord:
        """
        Log a tool call with full context.

        Args:
            tool_name: Name of the tool (e.g., "terminal → bash")
            args: Tool arguments
            output: Tool output content
            duration: Execution time in seconds
            status: "success", "error", "timeout", "cancelled"
            error: Error message if failed
            error_traceback: Full traceback if available
            metadata: Additional metadata (e.g., tool_call_id, summary)
            context: Execution context (e.g., previous user message)

        Returns:
            ToolCallRecord instance
        """
        if not self.session_log_file:
            raise RuntimeError("No active session. Call start_session() first.")

        self.call_count += 1

        # Create record
        record = ToolCallRecord(
            tool_name=tool_name,
            args=args,
            output=output,
            duration=duration,
            status=status,
            error=error,
            metadata=metadata,
            context=context
        )

        # Save full output if large
        output_file = None
        if output and len(output) > 1000:
            timestamp = int(time.time() * 1000)
            safe_tool_name = tool_name.replace(':', '_').replace('/', '_').replace(' ', '_')
            output_file = self.outputs_dir / f"{safe_tool_name}_{timestamp}.txt"

            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(f"# Tool Call Output\n")
                f.write(f"# Session: {self.session_id}\n")
                f.write(f"# Tool: {tool_name}\n")
                f.write(f"# Timestamp: {record.timestamp}\n")
                f.write(f"# Status: {status}\n")
                f.write(f"# Args: {json.dumps(args, indent=2)}\n")
                f.write(f"# Duration: {duration:.3f}s\n")
                f.write(f"#\n")
                f.write(f"# This file contains the full output of the tool call.\n")
                f.write(f"# Truncated version is in the main log.\n")
                f.write(f"\n{'=' * 80}\n\n")
                f.write(output)

            record.metadata['output_file'] = str(output_file)

        # Add error details if present
        if error_traceback:
            record.metadata['error_traceback'] = error_traceback

        # Write to session log
        log_entry = record.to_dict()
        log_entry['_call_number'] = self.call_count

        with open(self.session_log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

        return record

    def end_session(self, metadata: Optional[Dict] = None):
        """
        End the current session.

        Args:
            metadata: Optional metadata (e.g., final stats)
        """
        if not self.session_log_file:
            return

        footer = {
            "_type": "session_end",
            "session_id": self.session_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "total_calls": self.call_count,
            "metadata": metadata or {}
        }

        with open(self.session_log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(footer, ensure_ascii=False, indent=2) + '\n')

        self.session_id = None
        self.session_log_file = None
        self.call_count = 0

    def search_calls(
        self,
        session_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Search tool calls.

        Args:
            session_id: Filter by session (None = all sessions)
            tool_name: Filter by tool name (supports partial match)
            status: Filter by status ("success", "error", etc.)
            limit: Maximum results to return

        Returns:
            List of matching tool call records
        """
        results = []

        # Determine which files to search
        if session_id:
            log_files = [self.log_dir / f"{session_id}.jsonl"]
        else:
            # Search all session files, newest first
            log_files = sorted(
                self.log_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

        for log_file in log_files:
            if not log_file.exists():
                continue

            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line)

                        # Skip session headers/footers
                        if entry.get('_type') in ('session_start', 'session_end'):
                            continue

                        # Apply filters
                        if tool_name and tool_name.lower() not in entry.get('tool_name', '').lower():
                            continue

                        if status and entry.get('status') != status:
                            continue

                        results.append(entry)

                        if len(results) >= limit:
                            return results

                    except json.JSONDecodeError:
                        continue

        return results

    def get_recent_calls(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get most recent tool calls across all sessions.

        Args:
            limit: Number of calls to return

        Returns:
            List of recent tool call records
        """
        return self.search_calls(limit=limit)

    def get_failed_calls(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get recent failed tool calls for debugging.

        Args:
            limit: Number of calls to return

        Returns:
            List of failed tool call records
        """
        return self.search_calls(status="error", limit=limit)

    def read_full_output(self, output_file: str) -> str:
        """
        Read full output from a saved output file.

        Args:
            output_file: Path to output file

        Returns:
            Full output content
        """
        with open(output_file, 'r', encoding='utf-8') as f:
            # Skip header comments
            lines = f.readlines()
            start_idx = 0
            for i, line in enumerate(lines):
                if line.strip() == '=' * 80:
                    start_idx = i + 2  # Skip separator and blank line
                    break

            return ''.join(lines[start_idx:])


# Global singleton instance
_logger_instance: Optional[ToolLogger] = None


def get_tool_logger() -> ToolLogger:
    """Get or create the global tool logger instance."""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = ToolLogger()
    return _logger_instance
