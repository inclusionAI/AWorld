"""
Output manager for smart tool result display and file redirection.

Provides Claude Code style output formatting with:
- Compact display with visual symbols (⏺ ▶ ⎿)
- Smart truncation and folding
- Automatic file redirection suggestions
"""
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Optional


class OutputManager:
    """Manage tool output display with smart truncation and redirection."""

    # Thresholds for output management
    FOLD_LINE_THRESHOLD = 20  # Lines before showing fold indicator
    SAVE_LINE_THRESHOLD = 100  # Lines before suggesting file save
    MAX_PREVIEW_LINES = 15  # Maximum lines to show in preview
    MAX_LINE_LENGTH = 120  # Maximum characters per line before truncation

    def __init__(self, temp_dir: Optional[str] = None):
        """
        Initialize output manager.

        Args:
            temp_dir: Directory for saving large outputs (default: /tmp/aworld_outputs)
        """
        self.temp_dir = temp_dir or "/tmp/aworld_outputs"
        Path(self.temp_dir).mkdir(parents=True, exist_ok=True)

    def should_fold(self, output: str) -> bool:
        """Check if output should be folded."""
        lines = output.split('\n')
        return len(lines) > self.FOLD_LINE_THRESHOLD

    def should_save_to_file(self, output: str) -> bool:
        """Check if output should be saved to file."""
        lines = output.split('\n')
        return len(lines) > self.SAVE_LINE_THRESHOLD

    def format_tool_output(
        self,
        tool_name: str,
        output: str,
        save_to_file: bool = False
    ) -> Tuple[List[str], Optional[str]]:
        """
        Format tool output with Claude Code style.

        Args:
            tool_name: Name of the tool
            output: Raw output content
            save_to_file: Whether to save large output to file

        Returns:
            Tuple of (display_lines, file_path)
            - display_lines: Lines to display in console
            - file_path: Path to saved file if output was saved, None otherwise
        """
        lines = output.split('\n')
        total_lines = len(lines)
        file_path = None

        # Check if output should be saved
        if save_to_file or self.should_save_to_file(output):
            file_path = self._save_output(tool_name, output)

        display_lines = []

        # Tool call header with symbol
        display_lines.append(f"⏺ {tool_name}")

        # Format output content
        if total_lines == 0 or (total_lines == 1 and not output.strip()):
            # No output
            display_lines.append("  ⎿  [dim italic]No output[/dim italic]")
        elif total_lines <= self.MAX_PREVIEW_LINES:
            # Short output: show all with proper indentation
            for i, line in enumerate(lines):
                prefix = "  ⎿  " if i == 0 else "     "
                truncated_line = self._truncate_line(line)
                display_lines.append(f"{prefix}{truncated_line}")
        else:
            # Long output: show preview with fold indicator
            for i in range(self.MAX_PREVIEW_LINES):
                prefix = "  ⎿  " if i == 0 else "     "
                truncated_line = self._truncate_line(lines[i])
                display_lines.append(f"{prefix}{truncated_line}")

            # Add fold indicator
            remaining = total_lines - self.MAX_PREVIEW_LINES
            if file_path:
                display_lines.append(f"     [dim]… +{remaining} lines (saved to {file_path})[/dim]")
            else:
                display_lines.append(f"     [dim]… +{remaining} lines[/dim]")

        return display_lines, file_path

    def suggest_redirection(self, command: str) -> Optional[str]:
        """
        Suggest redirected version of command for large output.

        Args:
            command: Original command

        Returns:
            Suggested command with redirection, or None if not applicable
        """
        # Skip if already has redirection
        if '>' in command or '|' in command:
            return None

        # Commands that typically produce large output
        large_output_commands = ['ls', 'find', 'git log', 'git status', 'grep', 'cat']

        for cmd in large_output_commands:
            if command.strip().startswith(cmd):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_file = f"{self.temp_dir}/{cmd.replace(' ', '_')}_{timestamp}.txt"
                return f"{command} > {output_file} && echo 'Output saved to {output_file}' && wc -l {output_file}"

        return None

    def format_bash_command_suggestion(self, original_cmd: str) -> str:
        """
        Format bash command with output management best practices.

        Args:
            original_cmd: Original command

        Returns:
            Formatted command with redirection and piping suggestions
        """
        suggestions = []

        # Suggest head/tail for listing commands
        if any(original_cmd.startswith(cmd) for cmd in ['ls', 'find', 'git log']):
            suggestions.append(f"{original_cmd} | head -20")

        # Suggest wc for counting
        if 'grep' in original_cmd or 'find' in original_cmd:
            suggestions.append(f"{original_cmd} | wc -l")

        # Suggest redirection for large output
        redirect = self.suggest_redirection(original_cmd)
        if redirect:
            suggestions.append(redirect)

        if suggestions:
            return "\n".join([
                "💡 Output management suggestions:",
                *[f"   {i+1}. {s}" for i, s in enumerate(suggestions)]
            ])

        return ""

    def _truncate_line(self, line: str) -> str:
        """Truncate line if too long."""
        if len(line) <= self.MAX_LINE_LENGTH:
            return line
        return line[:self.MAX_LINE_LENGTH] + "…"

    def _save_output(self, tool_name: str, output: str) -> str:
        """
        Save output to temporary file.

        Args:
            tool_name: Name of the tool
            output: Output content

        Returns:
            Path to saved file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_tool_name = tool_name.replace(':', '_').replace('/', '_')
        filename = f"{safe_tool_name}_{timestamp}.txt"
        filepath = os.path.join(self.temp_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(output)

        return filepath

    def format_compact_summary(
        self,
        tool_name: str,
        output: str,
        max_lines: int = 3
    ) -> str:
        """
        Format a compact one-line summary of tool output.

        Args:
            tool_name: Name of the tool
            output: Output content
            max_lines: Maximum lines to preview

        Returns:
            Compact summary string
        """
        lines = [l.strip() for l in output.split('\n') if l.strip()]
        total_lines = len(lines)

        if total_lines == 0:
            return f"⚡ {tool_name} → [dim]no output[/dim]"
        elif total_lines == 1:
            truncated = self._truncate_line(lines[0])
            return f"⚡ {tool_name} → {truncated}"
        else:
            preview = " | ".join(lines[:max_lines])
            if len(preview) > self.MAX_LINE_LENGTH:
                preview = preview[:self.MAX_LINE_LENGTH] + "…"
            if total_lines > max_lines:
                return f"⚡ {tool_name} → {preview} [dim](+{total_lines - max_lines} more)[/dim]"
            return f"⚡ {tool_name} → {preview}"
