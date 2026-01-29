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
        self._init_session_management()
        self._setup_logging()
    
    # ========== Session Management (通用能力) ==========
    
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
    
    # ========== Output Rendering (通用能力) ==========
    
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
    
    def _render_tool_result_output(self, output) -> None:
        """
        Render ToolResultOutput to console with collapsible content for long results.
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
        
        if not result_content:
            self.console.print(f"[yellow]🔧 Tool: {tool_info}[/yellow]")
            return
        
        # Render based on content length. Use env for limits so long results (e.g. PPT outline JSON) display fully.
        content_length = len(result_content)
        max_preview_length = int(os.environ.get("AWORLD_CLI_TOOL_RESULT_MAX_CHARS", "20000"))
        max_preview_lines = int(os.environ.get("AWORLD_CLI_TOOL_RESULT_MAX_LINES", "200"))
        
        if content_length > max_preview_length:
            # Show preview for long content
            lines = result_content.split('\n')
            if len(lines) > max_preview_lines:
                # Show first few lines as preview
                preview_lines = lines[:max_preview_lines]
                preview_content = '\n'.join(preview_lines)
                remaining_lines = len(lines) - max_preview_lines
                preview_content += f"\n\n[dim]... ({remaining_lines} more lines, {content_length - len(preview_content)} more characters) ...[/dim]"
            else:
                # Show truncated preview
                preview_content = result_content[:max_preview_length] + f"\n\n[dim]... ({content_length - max_preview_length} more characters) ...[/dim]"
            
            tool_panel = Panel(
                preview_content,
                title=f"[bold yellow]🔧 Tool Result: {tool_info}[/bold yellow]",
                title_align="left",
                border_style="yellow",
                padding=(1, 2)
            )
        else:
            # Short content, display directly
            tool_panel = Panel(
                result_content,
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
    
    # ========== Logging (通用能力) ==========
    
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
    
    # ========== Abstract Methods (子类实现) ==========
    
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
