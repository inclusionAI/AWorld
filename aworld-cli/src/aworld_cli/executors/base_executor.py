"""
Base executor with common capabilities.

Provides:
- Session management (history, restore, list)
- Output rendering (Rich formatting, tool visualization)
- Logging configuration

Subclasses only need to implement execution logic.
"""
import os
import sys
import uuid
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

from rich.console import Console
from rich.markup import escape as markup_escape
from aworld.logs.util import logger
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.status import Status

# Try to import Group from rich.console, with fallback for older Rich versions
try:
    from rich.console import Group
except ImportError:
    # Fallback for older Rich versions - try importing from rich directly
    try:
        from rich import Group
    except ImportError:
        # If Group is not available, create a simple wrapper class
        # Group is used to combine multiple renderables
        class Group:
            """Fallback Group class for older Rich versions."""
            def __init__(self, *renderables):
                self.renderables = renderables
            
            def __rich_console__(self, console, options):
                from rich.console import RenderableType
                for renderable in self.renderables:
                    yield renderable

from .base import AgentExecutor


class BaseAgentExecutor(ABC, AgentExecutor):
    """
    Base executor with common capabilities.
    
    Provides:
    - Session management (history, restore, list)
    - Output rendering (Rich formatting, tool visualization)
    - Logging configuration
    
    Subclasses only need to implement execution logic.
    
    Example:
        class MyExecutor(BaseAgentExecutor):
            async def chat(self, message: Union[str, tuple[str, List[str]]]) -> str:
                # Implementation
                return response
    """
    
    def __init__(
        self,
        console: Optional[Console] = None,
        session_id: Optional[str] = None
    ):
        """
        Initialize base executor.

        Args:
            console: Optional Rich console for output
            session_id: Optional session ID. If None, will generate one automatically.
        """
        self.console = console or Console()
        self.session_id = session_id or self._generate_session_id()
        # Initialize content collapse states (adaptive display for CLI)
        self._collapsed_sections = {
            'message': False,   # 💬 message content - show full by default
            'tools': False,     # 🔧 tool calls content - show full by default
            'results': True     # ⚡ tool results content - collapse by default (often verbose)
        }
        self._init_session_management()
        self._setup_logging()
    
    # ========== Session Management (Common Capabilities) ==========
    
    def _init_session_management(self) -> None:
        """Initialize session history management."""
        self._session_history_file = self._get_session_history_file()
        self._session_history = self._load_session_history()
        if self.session_id not in self._session_history:
            self._add_session_to_history(self.session_id)
    
    def _generate_session_id(self) -> str:
        """
        Generate a new session ID.
        
        Returns:
            A new session ID string
            
        Example:
            >>> executor = BaseAgentExecutor()
            >>> new_id = executor._generate_session_id()
        """
        return f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    
    def _get_session_history_file(self) -> Path:
        """
        Get the path to session history file.
        
        Returns:
            Path to session history file
        """
        workspace_base = Path.cwd() / ".aworld" / "workspaces"
        workspace_base.mkdir(parents=True, exist_ok=True)
        return workspace_base / ".session_history.json"
    
    def _load_session_history(self) -> Dict[str, Dict]:
        """
        Load session history from file.
        
        Returns:
            Dictionary mapping session_id to session metadata
        """
        if not self._session_history_file.exists():
            return {}
        
        try:
            with open(self._session_history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            if self.console:
                self.console.print(f"[yellow]⚠️ Failed to load session history: {e}[/yellow]")
            return {}
    
    def _save_session_history(self) -> None:
        """Save session history to file."""
        try:
            with open(self._session_history_file, 'w', encoding='utf-8') as f:
                json.dump(self._session_history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            if self.console:
                self.console.print(f"[yellow]⚠️ Failed to save session history: {e}[/yellow]")
    
    def _add_session_to_history(self, session_id: str) -> None:
        """
        Add a session to history.
        
        Args:
            session_id: Session ID to add
        """
        self._session_history[session_id] = {
            "session_id": session_id,
            "created_at": datetime.now().isoformat(),
            "last_used_at": datetime.now().isoformat()
        }
        self._save_session_history()
    
    def _update_session_last_used(self, session_id: str) -> None:
        """
        Update the last used time for a session.
        
        Args:
            session_id: Session ID to update
        """
        if session_id in self._session_history:
            self._session_history[session_id]["last_used_at"] = datetime.now().isoformat()
            self._save_session_history()
    
    def get_latest_session_id(self) -> Optional[str]:
        """
        Get the most recently used session ID.
        
        Returns:
            Latest session ID or None if no history exists
            
        Example:
            >>> executor = BaseAgentExecutor()
            >>> latest_id = executor.get_latest_session_id()
        """
        if not self._session_history:
            return None
        
        # Sort by last_used_at in descending order
        sorted_sessions = sorted(
            self._session_history.items(),
            key=lambda x: x[1].get("last_used_at", ""),
            reverse=True
        )
        
        if sorted_sessions:
            return sorted_sessions[0][0]
        return None
    
    def list_sessions(self) -> List[Dict]:
        """
        List all sessions in history, sorted by last used time (newest first).
        
        Returns:
            List of session metadata dictionaries
            
        Example:
            >>> executor = BaseAgentExecutor()
            >>> sessions = executor.list_sessions()
        """
        if not self._session_history:
            return []
        
        # Sort by last_used_at in descending order
        sorted_sessions = sorted(
            self._session_history.values(),
            key=lambda x: x.get("last_used_at", ""),
            reverse=True
        )
        
        return sorted_sessions
    
    def restore_session(self, session_id: Optional[str] = None) -> str:
        """
        Restore to a specific session or the latest session.
        
        Args:
            session_id: Optional session ID to restore. If None, restores to the latest session.
            
        Returns:
            The restored session ID
            
        Example:
            >>> executor = BaseAgentExecutor()
            >>> # Restore to latest session
            >>> latest_id = executor.restore_session()
            >>> # Restore to specific session
            >>> specific_id = executor.restore_session("session_abc123")
        """
        if session_id is None:
            session_id = self.get_latest_session_id()
            if session_id is None:
                if self.console:
                    self.console.print("[yellow]⚠️ No session history found. Creating a new session.[/yellow]")
                return self.new_session()
        
        # Check if session exists in history
        if session_id not in self._session_history:
            if self.console:
                self.console.print(f"[yellow]⚠️ Session '{session_id}' not found in history. Creating a new session.[/yellow]")
            return self.new_session()
        
        # Restore to the session
        self.session_id = session_id
        self._update_session_last_used(session_id)
        
        if self.console:
            self.console.print(f"[green]✨ Restored to session: {self.session_id}[/green]")
        
        return self.session_id
    
    def new_session(self) -> str:
        """
        Create a new session and return the new session ID.
        
        Returns:
            The new session ID
            
        Example:
            >>> executor = BaseAgentExecutor()
            >>> old_id = executor.session_id
            >>> new_id = executor.new_session()
            >>> assert old_id != new_id
        """
        old_session_id = self.session_id
        self.session_id = self._generate_session_id()
        self._add_session_to_history(self.session_id)
        if self.console:
            self.console.print(f"[green]✨ New session created: {self.session_id}[/green]")
            if old_session_id:
                self.console.print(f"[dim]Previous session: {old_session_id}[/dim]")
        return self.session_id
    
    # ========== Output Rendering (Common Capabilities) ==========

    def _render_collapsible_content(self, section_type: str, header: str, content_lines: List[str], max_lines: int = 3) -> None:
        """
        Render collapsible content with expand/collapse functionality.

        Args:
            section_type: Type of section ('message', 'tools', 'results')
            header: Header text to display (e.g., "💬 AgentName")
            content_lines: List of content lines to display
            max_lines: Maximum lines to show when collapsed
        """
        if not content_lines:
            return

        is_collapsed = self._collapsed_sections.get(section_type, False)
        total_lines = len(content_lines)

        # Show header
        if total_lines > max_lines:
            # Add collapse/expand indicator
            indicator = "[dim]▼[/dim]" if not is_collapsed else "[dim]▶[/dim]"
            self.console.print(f"{header} {indicator}")
        else:
            # No collapse needed for short content
            self.console.print(header)

        # Show content with proper indentation for wrapped lines
        if is_collapsed and total_lines > max_lines:
            # Show only first few lines + summary
            for line in content_lines[:max_lines]:
                if line.strip():
                    self._print_indented_line(line)
                else:
                    self.console.print()
            self.console.print(f"   [dim italic]... ({total_lines - max_lines} more lines)[/dim italic]")
        else:
            # Show all content
            for line in content_lines:
                if line.strip():
                    self._print_indented_line(line)
                else:
                    self.console.print()

        # No extra spacing here - let caller control spacing

    def _print_indented_line(self, line: str, indent: str = "   ") -> None:
        """
        Print a line with proper indentation, handling line wrapping.

        When a line is too long and wraps to the next line, the wrapped
        portion should also be indented to maintain visual consistency.

        Args:
            line: The line to print
            indent: The indentation string (default: 3 spaces)
        """
        if not self.console:
            return

        # Get console width and calculate available width for content
        console_width = self.console.size.width if self.console.size else 80
        available_width = console_width - len(indent)

        # If line fits within available width, print normally
        if len(line) <= available_width:
            self.console.print(f"{indent}{line}")
            return

        # Handle long lines by splitting and indenting wrapped portions
        import textwrap

        # Wrap the line, preserving existing indentation in the original line
        wrapped_lines = textwrap.fill(
            line,
            width=available_width,
            subsequent_indent='',
            break_long_words=False,
            break_on_hyphens=False
        ).split('\n')

        # Print first line with original indent
        if wrapped_lines:
            self.console.print(f"{indent}{wrapped_lines[0]}")

            # Print subsequent lines with additional indentation
            for wrapped_line in wrapped_lines[1:]:
                self.console.print(f"{indent}{wrapped_line}")
    
    def _format_tool_call(self, tool_call, idx: int):
        """
        Format a single tool call into a readable string or Rich object.
        
        Args:
            tool_call: ToolCall object
            idx: Index of the tool call
            
        Returns:
            Formatted string representation or Rich object for PTC tools
        """
        from aworld.models.model_response import ToolCall
        
        if not tool_call:
            return ""
        
        # Get tool call information
        tool_id = getattr(tool_call, 'id', f'tool_call_{idx}')
        tool_type = getattr(tool_call, 'type', 'function')
        
        # Get function information
        function_name = "Unknown"
        function_args = ""
        
        if hasattr(tool_call, 'function') and tool_call.function:
            function_name = getattr(tool_call.function, 'name', 'Unknown')
            function_args = getattr(tool_call.function, 'arguments', '')
        
        # Special handling for PTC tool calls
        if function_name == "execute_ptc_code" or function_name.endswith("__execute_ptc_code"):
            # Parse arguments to extract code
            try:
                # Build Rich components for PTC
                header = Text()
                header.append("🔧 Tool #", style="bold cyan")
                header.append(f"{idx + 1}: ", style="bold cyan")
                header.append(f"{function_name}\n", style="bold")
                header.append("  ID: ", style="dim")
                header.append(f"{tool_id}\n", style="dim")
                header.append("  Type: ", style="dim")
                header.append(f"{tool_type}\n", style="dim")
                
                code = ""
                if function_args:
                    # Try to parse as JSON first
                    if isinstance(function_args, str):
                        try:
                            args_dict = json.loads(function_args)
                        except json.JSONDecodeError:
                            # If not valid JSON, treat as raw code string
                            code = function_args
                            args_dict = None
                    else:
                        args_dict = function_args
                    
                    # Extract code from dict if available
                    if isinstance(args_dict, dict):
                        code = args_dict.get('code', '') or args_dict.get('ptc_code', '') or ''
                    elif not code and isinstance(function_args, str):
                        # If function_args is a string but not JSON, use it as code
                        code = function_args
                
                # Format code with syntax highlighting
                if code and code.strip():
                    # Remove leading/trailing whitespace and normalize
                    code = code.strip()
                    # Create syntax-highlighted code block
                    try:
                        syntax = Syntax(
                            code,
                            "python",
                            theme="default",
                            line_numbers=True,
                            word_wrap=True,
                            padding=(1, 2)
                        )
                        # Wrap code in a panel for better visibility
                        code_panel = Panel(
                            syntax,
                            title="[bold yellow]🐍 Python Code[/bold yellow]",
                            title_align="left",
                            border_style="yellow",
                            padding=(0, 1)
                        )
                        
                        # Combine header and code panel
                        return Group(header, code_panel)
                    except Exception:
                        # If syntax highlighting fails, show code as plain text
                        header.append("  Code:\n", style="bold")
                        code_text = Text(code, style="dim")
                        code_panel = Panel(
                            code_text,
                            title="[bold yellow]🐍 Python Code[/bold yellow]",
                            title_align="left",
                            border_style="yellow",
                            padding=(0, 1)
                        )
                        return Group(header, code_panel)
                else:
                    # No code found
                    header.append("  Code: ", style="bold")
                    header.append("No code provided or code extraction failed\n", style="dim")
                    # Show raw arguments for debugging
                    if function_args:
                        header.append("  Raw arguments: ", style="dim")
                        header.append(f"{str(function_args)[:200]}...\n", style="dim")
                    return header
            except Exception as e:
                # Fallback to regular formatting if parsing fails
                header = Text()
                header.append("🔧 Tool #", style="bold cyan")
                header.append(f"{idx + 1}: ", style="bold cyan")
                header.append(f"{function_name}\n", style="bold")
                header.append("  ID: ", style="dim")
                header.append(f"{tool_id}\n", style="dim")
                header.append("  Type: ", style="dim")
                header.append(f"{tool_type}\n", style="dim")
                header.append("  Code: ", style="bold")
                header.append(f"Error parsing code: {str(e)}\n", style="red")
                if function_args:
                    header.append("  Raw arguments: ", style="dim")
                    header.append(f"{str(function_args)[:200]}...\n", style="dim")
                return header
        
        # Regular formatting for non-PTC tools
        # Format arguments
        try:
            if function_args:
                args_dict = json.loads(function_args) if isinstance(function_args, str) else function_args
                formatted_args = json.dumps(args_dict, indent=2, ensure_ascii=False)
            else:
                formatted_args = "No arguments"
        except (json.JSONDecodeError, TypeError):
            formatted_args = str(function_args) if function_args else "No arguments"
        
        # Build formatted string
        formatted = f"\n[bold]Tool #{idx + 1}:[/bold] {function_name}\n"
        formatted += f"[dim]  ID:[/dim] {tool_id}\n"
        formatted += f"[dim]  Type:[/dim] {tool_type}\n"
        if formatted_args and formatted_args != "No arguments":
            formatted += f"[bold]  Arguments:[/bold]\n{formatted_args}\n"
        
        return formatted
    
    def _format_tool_calls(self, tool_calls: list):
        """
        Format multiple tool calls into a readable string or Rich object.
        Excludes human tools from display as they require user interaction.
        
        Args:
            tool_calls: List of ToolCallOutput or ToolCall objects
            
        Returns:
            Formatted string representation or Rich Group object of all tool calls
        """
        from aworld.models.model_response import ToolCall
        
        if not tool_calls:
            return ""
        
        # Filter out human tools - they should not be displayed in tool calls panel
        filtered_tool_calls = []
        for tool_call_output in tool_calls:
            tool_call = None
            if hasattr(tool_call_output, 'data'):
                tool_call = tool_call_output.data
            elif isinstance(tool_call_output, ToolCall):
                tool_call = tool_call_output
            
            # Skip human tools
            if tool_call:
                function_name = ""
                if hasattr(tool_call, 'function') and tool_call.function:
                    function_name = getattr(tool_call.function, 'name', '')
                if 'human' not in function_name.lower():
                    filtered_tool_calls.append(tool_call_output)
        
        # If all tools are human tools, return empty string
        if not filtered_tool_calls:
            return ""
        
        # Build tool calls content (without title, title will be in Panel)
        tool_calls_content = []
        has_rich_objects = False
        
        for idx, tool_call_output in enumerate(filtered_tool_calls):
            tool_call = None
            # Extract ToolCall from ToolCallOutput
            if hasattr(tool_call_output, 'data'):
                tool_call = tool_call_output.data
            elif isinstance(tool_call_output, ToolCall):
                tool_call = tool_call_output
            
            if tool_call:
                formatted_part = self._format_tool_call(tool_call, idx)
                # Check if it's a Rich object (Group, Text, Panel, etc.)
                if isinstance(formatted_part, (Group, Text, Panel, Syntax)):
                    tool_calls_content.append(formatted_part)
                    has_rich_objects = True
                else:
                    # It's a string, convert to Text
                    tool_calls_content.append(Text(str(formatted_part)))
        
        # Wrap all tool calls in a red Panel
        if has_rich_objects:
            # Create a Group of all tool calls
            tool_calls_group = Group(*tool_calls_content)
            # Wrap in a red Panel with "Tool Calls:" title
            tool_calls_panel = Panel(
                tool_calls_group,
                title="[bold magenta]🔧 Tool Calls:[/bold magenta]",
                title_align="left",
                border_style="red",
                padding=(1, 2)
            )
            return tool_calls_panel
        else:
            # For string-based formatting, wrap in Panel too
            tool_calls_text = "\n".join(str(part) for part in tool_calls_content)
            tool_calls_panel = Panel(
                tool_calls_text,
                title="[bold magenta]🔧 Tool Calls:[/bold magenta]",
                title_align="left",
                border_style="red",
                padding=(1, 2)
            )
            return tool_calls_panel

    def _render_simple_message_output(self, output, answer: str, agent_name: str = None, is_handoff: bool = False) -> tuple[str, str]:
        """
        Simplified message output rendering with modern, clean Claude Code style.

        Features:
        - Remove heavy Panel borders
        - Use clean emoji and text markers
        - Reduce color usage, focus on content
        - Add whitespace for better readability
        - Show agent name and handoff notifications

        Args:
            output: MessageOutput instance
            answer: Current answer string
            agent_name: Name of the current agent
            is_handoff: Whether this is a handoff to a new agent

        Returns:
            Tuple of (updated_answer, rendered_content)
        """
        from aworld.output.base import MessageOutput

        if not isinstance(output, MessageOutput) or not self.console:
            return answer, ""

        # Extract agent name from metadata if not provided
        if not agent_name and hasattr(output, 'metadata') and output.metadata:
            agent_name = output.metadata.get('agent_name') or output.metadata.get('from_agent')

        # Default agent name
        if not agent_name:
            agent_name = "Assistant"

        # Extract content
        response_text = str(output.response) if hasattr(output, 'response') and output.response else ""
        reasoning_text = str(output.reasoning) if hasattr(output, 'reasoning') and output.reasoning else ""
        tool_calls = output.tool_calls if hasattr(output, 'tool_calls') and output.tool_calls else []

        # Update answer
        if response_text.strip():
            if not answer:
                answer = response_text
            elif response_text not in answer:
                answer = response_text

        # Build display content
        display_parts = []

        # Add main response with collapsible content
        if response_text.strip():
            # Prepare content lines for collapsible rendering
            response_lines = []

            # Add reasoning to response lines if available
            if reasoning_text.strip():
                response_lines.extend(["💭 Thinking process：", ""])
                reasoning_lines = reasoning_text.split('\n')
                for line in reasoning_lines:
                    if line.strip():
                        response_lines.append(f"{line}")
                    else:
                        response_lines.append("")
                response_lines.append("")  # Add spacing after reasoning

            # Add main response content
            content_lines = response_text.split('\n')
            for line in content_lines:
                response_lines.append(line)

            # Use collapsible rendering
            header = f"🤖 [bold]{agent_name}[/bold]"
            self._render_collapsible_content('message', header, response_lines, max_lines=10)
            self.console.print()  # Add spacing after message

        # Handle tool calls with collapsible display
        if tool_calls:
            # Filter out human tools
            filtered_tool_calls = []
            for tool_call_output in tool_calls:
                tool_call = None
                if hasattr(tool_call_output, 'data'):
                    tool_call = tool_call_output.data
                elif hasattr(tool_call_output, '__class__') and 'ToolCall' in str(tool_call_output.__class__):
                    tool_call = tool_call_output

                if tool_call:
                    function_name = ""
                    if hasattr(tool_call, 'function') and tool_call.function:
                        function_name = getattr(tool_call.function, 'name', '')
                    if 'human' not in function_name.lower():
                        filtered_tool_calls.append((tool_call_output, tool_call))

            # Render filtered tool calls with collapsible content
            if filtered_tool_calls:
                tool_lines = []

                for idx, (tool_call_output, tool_call) in enumerate(filtered_tool_calls):
                    function_name = "Unknown"
                    if hasattr(tool_call, 'function') and tool_call.function:
                        function_name = getattr(tool_call.function, 'name', 'Unknown')

                    # Add tool call entry with icon
                    tool_lines.append(f"▶ [cyan]{function_name}[/cyan]")

                    # Special handling for code execution
                    if function_name == "execute_ptc_code" or function_name.endswith("__execute_ptc_code"):
                        function_args = getattr(tool_call.function, 'arguments', '') if hasattr(tool_call, 'function') and tool_call.function else ''

                        try:
                            # Extract code
                            code = ""
                            if function_args:
                                if isinstance(function_args, str):
                                    try:
                                        args_dict = json.loads(function_args)
                                    except json.JSONDecodeError:
                                        code = function_args
                                        args_dict = None
                                else:
                                    args_dict = function_args

                                if isinstance(args_dict, dict):
                                    code = args_dict.get('code', '') or args_dict.get('ptc_code', '') or ''
                                elif not code and isinstance(function_args, str):
                                    code = function_args

                            # Add code content with proper indentation
                            if code and code.strip():
                                tool_lines.append("   [dim]Code：[/dim]")
                                code_lines = code.strip().split('\n')
                                for code_line in code_lines:
                                    tool_lines.append(f"      {code_line}")
                        except Exception:
                            # Fallback for parsing errors
                            tool_lines.append("   [dim red]Code parsing failed[/dim red]")
                    else:
                        # For non-code tools, display arguments
                        function_args = getattr(tool_call.function, 'arguments', '') if hasattr(tool_call, 'function') and tool_call.function else ''

                        if function_args:
                            try:
                                # Try to parse and format arguments nicely
                                if isinstance(function_args, str):
                                    try:
                                        args_dict = json.loads(function_args)
                                        if isinstance(args_dict, dict) and args_dict:
                                            tool_lines.append("   [dim]Arguments：[/dim]")
                                            for key, value in args_dict.items():
                                                # Truncate long values for readability
                                                if isinstance(value, str) and len(value) > 50:
                                                    display_value = value[:47] + "..."
                                                else:
                                                    display_value = str(value)
                                                tool_lines.append(f"      {key}: {display_value}")
                                    except json.JSONDecodeError:
                                        # If not valid JSON, show as raw text (truncated)
                                        if len(function_args) > 100:
                                            display_args = function_args[:97] + "..."
                                        else:
                                            display_args = function_args
                                        tool_lines.append("   [dim]Arguments：[/dim]")
                                        tool_lines.append(f"      {display_args}")
                                else:
                                    # Non-string arguments
                                    tool_lines.append("   [dim]Arguments：[/dim]")
                                    tool_lines.append(f"      {str(function_args)}")
                            except Exception:
                                # Fallback for any parsing errors
                                tool_lines.append("   [dim]Arguments：[/dim]")
                                tool_lines.append("      [dim red]Argument parsing failed[/dim red]")

                    tool_lines.append("")  # Add spacing between tools

                # Use collapsible rendering for tool calls
                header = "🔧 [bold]Tool calls[/bold]"
                self._render_collapsible_content('tools', header, tool_lines, max_lines=15)
                # No extra spacing here - let next element control spacing

        # Build message_content for return value
        message_parts = []
        if reasoning_text.strip():
            message_parts.append(f"Thinking process：{reasoning_text}")
        if response_text.strip():
            message_parts.append(response_text)
        if tool_calls:
            tool_summary = f"Used {len(tool_calls)} tools"
            message_parts.append(tool_summary)

        message_content = "\n\n".join(message_parts).strip()

        return answer, message_content

    def _render_message_output(self, output, answer: str) -> tuple[str, str]:
        """
        Render MessageOutput to console and extract answer.
        
        Args:
            output: MessageOutput instance
            answer: Current answer string
            
        Returns:
            Tuple of (updated_answer, rendered_content)
        """
        from aworld.output.base import MessageOutput
        
        if not isinstance(output, MessageOutput) or not self.console:
            return answer, ""
        
        # Extract content
        response_text = str(output.response) if hasattr(output, 'response') and output.response else ""
        reasoning_text = str(output.reasoning) if hasattr(output, 'reasoning') and output.reasoning else ""
        tool_calls = output.tool_calls if hasattr(output, 'tool_calls') and output.tool_calls else []
        
        # Update answer
        if response_text.strip():
            if not answer:
                answer = response_text
            elif response_text not in answer:
                answer = response_text
        
        # Render to console
        # If tool_calls is empty, use Markdown component for better formatting
        if not tool_calls and response_text.strip():
            # Use Markdown for rendering when there are no tool calls
            from rich.align import Align
            
            # Build content with reasoning if available
            content_parts = []
            if reasoning_text.strip():
                # Use Markdown for reasoning_text to maintain consistent formatting
                reasoning_markdown = Markdown(
                    f"💭 **Reasoning:**\n\n{reasoning_text}",
                    code_theme="default",
                    inline_code_theme="default"
                )
                content_parts.append(reasoning_markdown)
            
            # Use Markdown for response_text
            markdown_content = Markdown(
                response_text,
                code_theme="default",
                inline_code_theme="default"
            )
            content_parts.append(markdown_content)
            
            # Combine content using Group
            group_content = Group(*content_parts) if len(content_parts) > 1 else content_parts[0]
            
            # Align content to left
            panel_content = Align.left(group_content)
            
            # Render with Panel
            message_panel = Panel(
                panel_content,
                title="[bold cyan]💬 Agent Message[/bold cyan]",
                title_align="left",
                border_style="cyan",
                padding=(1, 2)
            )
            self.console.print(message_panel)
            self.console.print()
            
            # Build message_content for return value
            message_content = "\n\n".join([
                reasoning_text if reasoning_text.strip() else "",
                response_text
            ]).strip()
        else:
            # Build message content with tool calls
            content_parts = []
            
            # Add reasoning if available
            if reasoning_text.strip():
                content_parts.append(Text(f"[dim]💭 Reasoning:[/dim]\n{reasoning_text}\n", style="dim"))
            
            # Add response if available
            if response_text.strip():
                content_parts.append(Text(response_text))
            
            # Add tool calls
            tool_calls_formatted = None
            if tool_calls:
                tool_calls_formatted = self._format_tool_calls(tool_calls)
                if tool_calls_formatted:
                    # Check if tool_calls_formatted is a Rich object
                    if isinstance(tool_calls_formatted, (Group, Text, Panel, Syntax)):
                        content_parts.append(tool_calls_formatted)
                    else:
                        # Convert string to Text
                        content_parts.append(Text(str(tool_calls_formatted)))
            
            # Build message_content for return value (string representation)
            message_parts = []
            if reasoning_text.strip():
                message_parts.append(reasoning_text)
            if response_text.strip():
                message_parts.append(response_text)
            if tool_calls_formatted:
                message_parts.append(str(tool_calls_formatted))
            message_content = "\n\n".join(message_parts).strip()
            
            # Render to console using Rich objects
            if content_parts:
                # Create Group if multiple parts, otherwise use single part
                if len(content_parts) > 1:
                    panel_content = Group(*content_parts)
                else:
                    panel_content = content_parts[0]
                
                message_panel = Panel(
                    panel_content,
                    title="[bold cyan]💬 Agent Message[/bold cyan]",
                    title_align="left",
                    border_style="cyan",
                    padding=(1, 2)
                )
                self.console.print(message_panel)
                self.console.print()
        
        return answer, message_content

    def _filter_file_line_info(self, content: str) -> str:
        """
        Filter out file:line information and other unwanted text from tool result content.
        Removes patterns like "server.py:619", "main.py:123", "Processing request of type", etc.

        Args:
            content: Original content string

        Returns:
            Filtered content string
        """
        import re
        if not content:
            return content

        # Pattern to match file:line references (e.g., "server.py:619", "main.py:123")
        # Matches: filename.extension:number
        file_line_pattern = r'\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z0-9]+:\d+\b'

        # Remove file:line patterns
        filtered_content = re.sub(file_line_pattern, '', content)

        # Remove "Processing request of type" lines
        # This removes the entire line containing this text
        processing_pattern = r'.*Processing request of type.*\n?'
        filtered_content = re.sub(processing_pattern, '', filtered_content, flags=re.IGNORECASE)

        # Clean up extra whitespace and empty lines that might be left behind
        filtered_content = re.sub(r'\n\s*\n', '\n', filtered_content)  # Remove empty lines
        filtered_content = re.sub(r'\s+', ' ', filtered_content)  # Normalize whitespace
        filtered_content = filtered_content.strip()

        return filtered_content

    def _render_simple_tool_result_output(self, output) -> None:
        """
        Simplified tool result output rendering with modern, clean Claude Code style.

        Features:
        - Remove heavy Panel borders
        - Use clean emoji and text markers
        - Reduce color usage, focus on content
        - Add whitespace for better readability
        - Smart content truncation and summarization
        - Collapsible content display

        Args:
            output: ToolResultOutput instance
        """
        from aworld.output.base import ToolResultOutput

        if not isinstance(output, ToolResultOutput) or not self.console:
            return

        # Extract tool information
        tool_name = getattr(output, 'tool_name', 'Unknown Tool')
        action_name = getattr(output, 'action_name', '')
        tool_type = getattr(output, 'tool_type', '')

        # Skip rendering for human tools - user input doesn't need to be displayed
        if 'human' in tool_name.lower() or 'human' in action_name.lower():
            return

        # Get tool_call_id
        tool_call_id = ""
        if hasattr(output, 'metadata') and output.metadata:
            tool_call_id = output.metadata.get('tool_call_id', '')
        if not tool_call_id and hasattr(output, 'origin_tool_call') and output.origin_tool_call:
            tool_call_id = getattr(output.origin_tool_call, 'id', '')

        # Get summary from metadata first
        summary = None
        if hasattr(output, 'metadata') and output.metadata:
            summary = output.metadata.get('summary')

        # Get result content and filter file:line info
        result_content = ""
        if hasattr(output, 'data') and output.data:
            data_str = str(output.data)
            if data_str.strip():
                # Filter out file:line information
                result_content = self._filter_file_line_info(data_str)

        # Build simple tool info line
        tool_parts = []
        if tool_name:
            tool_parts.append(tool_name)
        if action_name and action_name != tool_name:
            tool_parts.append(f"→ {action_name}")
        tool_info = " ".join(tool_parts)

        # Determine what content to show
        display_content = None
        if summary:
            # Use provided summary and filter it too
            display_content = self._filter_file_line_info(summary)
        elif result_content:
            # Smart content truncation
            max_chars = int(os.environ.get("AWORLD_CLI_TOOL_RESULT_SUMMARY_MAX_CHARS", "300"))
            max_lines = int(os.environ.get("AWORLD_CLI_TOOL_RESULT_SUMMARY_MAX_LINES", "3"))

            lines = result_content.split('\n')

            # Check if it's JSON and try to format nicely
            is_json = False
            try:
                import json
                parsed = json.loads(result_content)
                if isinstance(parsed, dict):
                    # Show key info from JSON
                    key_info = []
                    for key, value in list(parsed.items())[:3]:  # First 3 keys
                        if isinstance(value, (str, int, float, bool)):
                            key_info.append(f"{key}: {value}")
                        elif isinstance(value, (list, dict)):
                            key_info.append(f"{key}: [{len(value)} items]" if isinstance(value, list) else f"{key}: {{object}}")

                    if key_info:
                        display_content = "\n".join(key_info)
                        if len(parsed) > 3:
                            display_content += f"\n... ({len(parsed) - 3} more fields)"
                        is_json = True
            except:
                pass

            # If not JSON or JSON parsing failed, use line-based truncation
            if not is_json:
                display_content = result_content

        # Use collapsible rendering for tool results
        if display_content:
            content_lines = display_content.split('\n')
            header = f"⚡ [bold]{tool_info}[/bold]"
            self._render_collapsible_content('results', header, content_lines, max_lines=3)
        else:
            # No content case - still show header but indicate no output
            self.console.print(f"⚡ [bold]{tool_info}[/bold]", style="dim")
            self.console.print("   [dim italic]No output[/dim italic]")
            self.console.print()

    def _render_tool_result_output(self, output) -> None:
        """
        Render ToolResultOutput to console with summary information by default.
        Skips rendering for human tools as their results are user input and don't need to be displayed.
        
        Args:
            output: ToolResultOutput instance
        """
        from aworld.output.base import ToolResultOutput
        
        if not isinstance(output, ToolResultOutput) or not self.console:
            return
        
        # Extract tool information
        tool_name = getattr(output, 'tool_name', 'Unknown Tool')
        action_name = getattr(output, 'action_name', '')
        tool_type = getattr(output, 'tool_type', '')
        
        # Skip rendering for human tools - user input doesn't need to be displayed as tool result
        if 'human' in tool_name.lower() or 'human' in action_name.lower():
            return
        
        # Get tool_call_id from metadata first, then from origin_tool_call
        tool_call_id = ""
        # Try to get from metadata (most reliable source)
        if hasattr(output, 'metadata') and output.metadata:
            tool_call_id = output.metadata.get('tool_call_id', '')
        # Fallback to origin_tool_call.id if not in metadata
        if not tool_call_id and hasattr(output, 'origin_tool_call') and output.origin_tool_call:
            tool_call_id = getattr(output.origin_tool_call, 'id', '')
        
        # Get summary from metadata first, fallback to generating a summary from data
        summary = None
        if hasattr(output, 'metadata') and output.metadata:
            summary = output.metadata.get('summary')
        
        # Get result content
        result_content = ""
        if hasattr(output, 'data') and output.data:
            data_str = str(output.data)
            if data_str.strip():
                result_content = data_str
        
        # Build tool info
        tool_info = f"[bold]{tool_name}[/bold]"
        if action_name:
            tool_info += f" → {action_name}"
        if tool_type:
            tool_info += f" [{tool_type}]"
        if tool_call_id:
            tool_info += f" [ID: {tool_call_id}]"
        
        # Default to showing summary only
        if summary:
            # Use provided summary
            display_content = summary
        elif result_content:
            # Generate a brief summary from content (first few lines or truncated)
            max_summary_length = int(os.environ.get("AWORLD_CLI_TOOL_RESULT_SUMMARY_MAX_CHARS", "500"))
            max_summary_lines = int(os.environ.get("AWORLD_CLI_TOOL_RESULT_SUMMARY_MAX_LINES", "5"))
            
            lines = result_content.split('\n')
            if len(lines) > max_summary_lines:
                # Show first few lines as summary
                summary_lines = lines[:max_summary_lines]
                display_content = '\n'.join(summary_lines)
                content_length = len(result_content)
                remaining_lines = len(lines) - max_summary_lines
                display_content += f"\n\n[dim]... ({remaining_lines} more lines, {content_length} total characters) ...[/dim]"
            elif len(result_content) > max_summary_length:
                # Show truncated summary
                display_content = result_content[:max_summary_length] + f"\n\n[dim]... ({len(result_content) - max_summary_length} more characters) ...[/dim]"
            else:
                # Short content, show as-is
                display_content = result_content
        else:
            # No content, just show tool info
            self.console.print(f"[yellow]🔧 Tool: {tool_info}[/yellow]")
            return
        
        # Render summary panel
        tool_panel = Panel(
            display_content,
            title=f"[bold yellow]🔧 Tool Result: {tool_info}[/bold yellow]",
            title_align="left",
            border_style="yellow",
            padding=(1, 2)
        )
        
        self.console.print(tool_panel)
        self.console.print()
    
    def _extract_answer_from_output(self, output) -> str:
        """
        Extract answer from various output types.
        
        Args:
            output: Output object of any type
            
        Returns:
            Extracted answer string, empty if not found
        """
        if hasattr(output, 'answer') and output.answer:
            return output.answer
        elif hasattr(output, 'payload') and hasattr(output.payload, 'answer'):
            return output.payload.answer
        elif isinstance(output, dict) and 'answer' in output:
            return output.get('answer', '')
        return ""
    
    # ========== Logging (Common Capabilities) ==========
    
    def _setup_logging(self) -> None:
        """
        Configure logging for CLI: disable console output, keep file output only.
        
        This method removes console/stderr handlers from loguru base logger while preserving 
        file handlers, so logs are only written to files in the logs/ directory.
        
        All AWorld loggers (logger, trace_logger, etc.) share the same base loguru logger,
        so removing stderr handler from base logger affects all of them.
        
        Example:
            >>> executor = BaseAgentExecutor()
            >>> executor._setup_logging()  # Console logs disabled, file logs preserved
        """
        try:
            from loguru import logger as base_loguru_logger
            from aworld.logs.instrument.loguru_instrument import _get_handlers
            
            # Get all current handlers from base logger
            handlers = _get_handlers(base_loguru_logger)
            
            # Remove all console/stderr handlers, keep file handlers
            for handler in handlers:
                if hasattr(handler, '_sink'):
                    sink = handler._sink
                    # Remove stderr handler (console output)
                    if sink == sys.stderr:
                        try:
                            base_loguru_logger.remove(handler._id)
                        except (ValueError, AttributeError):
                            pass
                    # Check if it's a file-like object pointing to stderr
                    elif hasattr(sink, 'name') and sink.name == '<stderr>':
                        try:
                            base_loguru_logger.remove(handler._id)
                        except (ValueError, AttributeError):
                            pass
            
            # Also try direct access to handlers dict as fallback
            if hasattr(base_loguru_logger, '_core'):
                core = getattr(base_loguru_logger, '_core')
                if hasattr(core, 'handlers'):
                    handlers_dict = getattr(core, 'handlers')
                    if isinstance(handlers_dict, dict):
                        for handler_id, handler in list(handlers_dict.items()):
                            if hasattr(handler, '_sink') and handler._sink == sys.stderr:
                                try:
                                    base_loguru_logger.remove(handler_id)
                                except (ValueError, AttributeError):
                                    pass
            
        except ImportError:
            # Fallback: if loguru_instrument is not available
            try:
                from loguru import logger as loguru_logger
                # Try to remove stderr handlers from base logger
                try:
                    if hasattr(loguru_logger, '_core'):
                        core = getattr(loguru_logger, '_core')
                        if hasattr(core, 'handlers'):
                            handlers_dict = getattr(core, 'handlers')
                            if isinstance(handlers_dict, dict):
                                for handler_id, handler in list(handlers_dict.items()):
                                    if hasattr(handler, '_sink') and handler._sink == sys.stderr:
                                        try:
                                            loguru_logger.remove(handler_id)
                                        except (ValueError, AttributeError):
                                            pass
                except Exception:
                    pass
            except ImportError:
                # Fallback to standard logging if loguru is not available
                logging.getLogger().setLevel(logging.ERROR)
                logging.getLogger("aworld").setLevel(logging.ERROR)
                logging.getLogger("aworld.core").setLevel(logging.ERROR)
                logging.getLogger("aworld.memory").setLevel(logging.ERROR)
                logging.getLogger("aworld.output").setLevel(logging.ERROR)
        except Exception:
            # If anything goes wrong, just suppress console output at standard logging level
            logging.getLogger().setLevel(logging.ERROR)
            logging.getLogger("aworld").setLevel(logging.ERROR)
    
    # ========== Abstract Methods (Subclass Implementation) ==========
    
    @abstractmethod
    async def chat(self, message: Union[str, tuple[str, List[str]]]) -> str:
        """
        Execute a chat message and return the response.
        
        Args:
            message: User message to process (string or tuple of (text, image_urls) for multimodal)
                    Multimodal format: (text, [image_data_url1, image_data_url2, ...])
            
        Returns:
            Agent response as string
        """
        pass


__all__ = ["BaseAgentExecutor"]
