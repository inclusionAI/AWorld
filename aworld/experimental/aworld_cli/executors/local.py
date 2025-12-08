"""
Local agent executor.
"""
import os
import sys
import asyncio
import logging
import traceback
import uuid
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.tree import Tree
from rich.status import Status
from rich.status import Status
from rich.live import Live

from aworld.config import TaskConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel
from aworld.core.task import Task
from aworld.runner import Runners
from .base import AgentExecutor

# Try to import WorkSpace for local workspace creation
try:
    from aworld.output import WorkSpace
except ImportError:
    WorkSpace = None

# Try to import init_middlewares, fallback to no-op if not available
try:
    from aworld.core.context.amni.config import init_middlewares
except ImportError:
    # Fallback: init_middlewares might not be available in all environments
    def init_middlewares():
        """No-op fallback for init_middlewares if not available."""
        pass


class LocalAgentExecutor(AgentExecutor):
    """Executor for local agents."""
    
    def __init__(
        self, 
        swarm: Swarm, 
        context_config=None,
        console: Optional[Console] = None,
        session_id: Optional[str] = None
    ):
        """
        Initialize local agent executor.
        
        Args:
            swarm: Swarm instance from agent team
            context_config: Context configuration for ApplicationContext. If None, will use default config.
            console: Optional Rich console for output
            session_id: Optional session ID. If None, will generate one automatically.
            
        Example:
            >>> executor = LocalAgentExecutor(swarm)
            >>> response = await executor.chat("Hello")
        """
        self.swarm = swarm
        self.context_config = context_config
        self.console = console
        # Initialize session_id: use provided one or generate a new one
        self.session_id = session_id or self._generate_session_id()
        # Initialize session history management
        self._session_history_file = self._get_session_history_file()
        self._session_history = self._load_session_history()
        # Add current session to history if not exists
        if self.session_id not in self._session_history:
            self._add_session_to_history(self.session_id)
    
    def _generate_session_id(self) -> str:
        """
        Generate a new session ID.
        
        Returns:
            A new session ID string
            
        Example:
            >>> executor = LocalAgentExecutor(swarm)
            >>> new_id = executor._generate_session_id()
        """
        return f"session_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
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
        """
        Save session history to file.
        """
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
            >>> executor = LocalAgentExecutor(swarm)
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
            >>> executor = LocalAgentExecutor(swarm)
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
            >>> executor = LocalAgentExecutor(swarm)
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
            >>> executor = LocalAgentExecutor(swarm)
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
    
    async def _build_task(
        self, 
        task_content: str, 
        session_id: str = None, 
        task_id: str = None
    ) -> Task:
        """
        Build task from task content.
        
        Args:
            task_content: Task content string
            session_id: Optional session ID. If None, will use the executor's current session_id.
            task_id: Optional task ID. If None, will generate one.
            
        Returns:
            Task instance
        """
        # Use executor's session_id if not provided
        if not session_id:
            session_id = self.session_id
        
        if not task_id:
            task_id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # 1. Build task input
        task_input = TaskInput(
            user_id="user",
            session_id=session_id,
            task_id=task_id,
            task_content=task_content,
            origin_user_input=task_content
        )
        
        # 2. Build context config if not provided
        if not self.context_config:
            self.context_config = AmniConfigFactory.create(
                AmniConfigLevel.NAVIGATOR,
                debug_mode=True
            )
            self.context_config.agent_config.history_scope = "session"
        
        # 3. Build workspace
        workspace = await self._create_workspace(session_id)
        
        # 4. Build context
        async def build_context(_task_input: TaskInput, _swarm: Swarm, _workspace) -> ApplicationContext:
            """Build application context from task input and swarm."""
            _context = await ApplicationContext.from_input(
                _task_input, 
                workspace=_workspace,
                context_config=self.context_config
            )
            await _context.init_swarm_state(_swarm)
            return _context
        
        context = await build_context(task_input, self.swarm, workspace)
        
        # 5. Build task with context
        return Task(
            id=context.task_id,
            user_id=context.user_id,
            session_id=context.session_id,
            input=task_input.task_content,
            endless_threshold=5,
            swarm=self.swarm,
            context=context,
            conf=TaskConfig(
                stream=False,
                exit_on_failure=True
            ),
            timeout=60 * 60
        )
    
    async def chat(self, message: str) -> str:
            """
            Execute chat with local agent using Task/Runners pattern.
            
            Args:
                message: User message
                
            Returns:
                Agent response
                
            Example:
                >>> executor = LocalAgentExecutor(swarm)
                >>> response = await executor.chat("Hello")
            """
            # 0. Ensure console logging is disabled (environment variable should already be set in main.py)
            # But we still call _setup_logging as a safety measure
            self._setup_logging()
            
            # 1. Init middlewares
            load_dotenv()
            init_middlewares()
            
            # 2. Build task (will use current session_id)
            # Update session last used time
            self._update_session_last_used(self.session_id)
            task = await self._build_task(message, session_id=self.session_id)
            
            # 3. Run task with streaming
            try:
                if self.console:
                    self.console.print(f"[dim]🔄 Running task: {task.id}[/dim]")
                
                # Get streaming outputs
                outputs = Runners.streamed_run_task(task=task)

                # Process stream events
                answer = ""
                last_message_output = None
                
                async def consume_stream():
                    """Consume stream events and collect outputs with beautiful formatting."""
                    nonlocal answer, last_message_output
                    loading_status = None
                    
                    def _start_loading_status(message: str):
                        """Start or update loading status."""
                        nonlocal loading_status
                        if not self.console:
                            return
                        if loading_status:
                            loading_status.update(f"[dim]{message}[/dim]")
                        else:
                            loading_status = Status(f"[dim]{message}[/dim]", console=self.console)
                            loading_status.start()
                    
                    def _stop_loading_status():
                        """Stop loading status."""
                        nonlocal loading_status
                        if loading_status:
                            loading_status.stop()
                            loading_status = None
                    
                    try:
                        from aworld.output.base import MessageOutput, ToolResultOutput
                        
                        # Show loading status while waiting for first output
                        _start_loading_status("💭 Thinking...")
                        
                        try:
                            async for output in outputs.stream_events():
                                if not self.console:
                                    continue
                                
                                # Handle MessageOutput
                                if isinstance(output, MessageOutput):
                                    # Stop thinking status before rendering message
                                    _stop_loading_status()
                                    
                                    last_message_output = output
                                    answer, _ = self._render_message_output(output, answer)
                                    
                                    # Check if there are tool calls - if so, show "Calling tool..." status
                                    tool_calls = output.tool_calls if hasattr(output, 'tool_calls') and output.tool_calls else []
                                    if tool_calls:
                                        # Has tool calls, will execute tools next
                                        _start_loading_status("🔧 Calling tool...")
                                    # If no tool calls, don't show thinking status here
                                    # It might be final response, or next output will trigger thinking status
                                
                                # Handle ToolResultOutput
                                elif isinstance(output, ToolResultOutput):
                                    # Stop "Calling tool..." status before rendering result
                                    _stop_loading_status()
                                    
                                    # Render tool result
                                    self._render_tool_result_output(output)
                                    
                                    # Immediately show thinking status after tool execution completes
                                    # Agent will process the tool result and think about next steps
                                    _start_loading_status("💭 Thinking...")
                                
                                # Handle other output types
                                else:
                                    # Stop any loading status
                                    _stop_loading_status()
                                    
                                    # Try to extract answer
                                    extracted_answer = self._extract_answer_from_output(output)
                                    if extracted_answer:
                                        answer = extracted_answer or answer
                                    
                                    # Show generic output if it has meaningful content
                                    if hasattr(output, 'data') and output.data:
                                        data_str = str(output.data)
                                        if data_str.strip() and len(data_str) > 10:
                                            generic_panel = Panel(
                                                data_str[:500],
                                                title=f"[dim]📦 {type(output).__name__}[/dim]",
                                                border_style="dim",
                                                padding=(1, 2)
                                            )
                                            self.console.print(generic_panel)
                                            self.console.print()
                        finally:
                            # Ensure loading status is stopped
                            _stop_loading_status()
                    
                    except Exception as e:
                        # Stop loading status on error
                        if loading_status:
                            loading_status.stop()
                        if self.console:
                            error_panel = Panel(
                                f"Error in stream consumption: {str(e)}",
                                title="[bold red]❌ Stream Error[/bold red]",
                                border_style="red",
                                padding=(1, 2)
                            )
                            self.console.print(error_panel)
                            self.console.print()
                        raise
                
                # Consume all stream events
                await consume_stream()
                
                # Try to get final result if task is still running
                # Note: After stream_events() completes, the task may be cancelled, so we handle CancelledError
                if hasattr(outputs, '_run_impl_task') and outputs._run_impl_task and not outputs.is_complete:
                    try:
                        # Wait with timeout to avoid hanging
                        final_result = await asyncio.wait_for(outputs._run_impl_task, timeout=1.0)
                        if self.console:
                            self.console.print(f"[dim]📋 Final result received: {type(final_result)}[/dim]")
                        
                        # Extract answer from final result
                        if final_result and isinstance(final_result, dict):
                            if task.id in final_result:
                                task_response = final_result[task.id]
                                if self.console:
                                    self.console.print(f"[dim]📋 TaskResponse type: {type(task_response)}[/dim]")
                                
                                # Try different ways to get the answer
                                if hasattr(task_response, 'answer'):
                                    answer = task_response.answer or answer
                                    if self.console:
                                        self.console.print(f"[dim]✅ Got answer from .answer attribute[/dim]")
                                elif isinstance(task_response, dict):
                                    answer = task_response.get('answer', '') or answer
                                    if self.console:
                                        self.console.print(f"[dim]✅ Got answer from dict[/dim]")
                                else:
                                    answer = str(task_response) if task_response else answer
                                    if self.console:
                                        self.console.print(f"[dim]✅ Got answer from str conversion[/dim]")
                            else:
                                if self.console:
                                    self.console.print(f"[yellow]⚠️ Task ID '{task.id}' not found in result[/yellow]")
                                    self.console.print(f"[dim]Available keys: {list(final_result.keys())}[/dim]")
                    except asyncio.CancelledError:
                        # Task was cancelled, which is normal after stream completes
                        # No need to display this to the user as it's expected behavior
                        pass
                    except asyncio.TimeoutError:
                        # Task is still running, but we'll use what we have
                        if self.console:
                            self.console.print(f"[dim]ℹ️ Task still running, using streamed answer[/dim]")
                    except Exception as e:
                        if self.console:
                            self.console.print(f"[yellow]⚠️ Error waiting for final result: {e}[/yellow]")
                
                # Return answer without printing (already displayed in stream)
                return answer
                
            except Exception as err:
                error_msg = f"Error: {err}, traceback: {traceback.format_exc()}"
                if self.console:
                    self.console.print(f"[red]❌ {error_msg}[/red]")
                raise
    
    def _setup_logging(self):
        """
        Configure logging for CLI: disable console output, keep file output only.
        
        This method removes console/stderr handlers from loguru base logger while preserving 
        file handlers, so logs are only written to files in the logs/ directory.
        
        All AWorld loggers (logger, trace_logger, etc.) share the same base loguru logger,
        so removing stderr handler from base logger affects all of them.
        
        Example:
            >>> executor = LocalAgentExecutor(swarm)
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
    
    def _format_tool_call(self, tool_call, idx: int) -> str:
        """
        Format a single tool call into a readable string.
        
        Args:
            tool_call: ToolCall object
            idx: Index of the tool call
            
        Returns:
            Formatted string representation of the tool call
        """
        from aworld.models.model_response import ToolCall
        import json
        
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
    
    def _format_tool_calls(self, tool_calls: list) -> str:
        """
        Format multiple tool calls into a readable string.
        
        Args:
            tool_calls: List of ToolCallOutput or ToolCall objects
            
        Returns:
            Formatted string representation of all tool calls
        """
        from aworld.models.model_response import ToolCall
        
        if not tool_calls:
            return ""
        
        formatted_content = "[bold magenta]🔧 Tool Calls:[/bold magenta]\n"
        
        for idx, tool_call_output in enumerate(tool_calls):
            tool_call = None
            # Extract ToolCall from ToolCallOutput
            if hasattr(tool_call_output, 'data'):
                tool_call = tool_call_output.data
            elif isinstance(tool_call_output, ToolCall):
                tool_call = tool_call_output
            
            if tool_call:
                formatted_content += self._format_tool_call(tool_call, idx)
        
        return formatted_content
    
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
            from rich.markdown import Markdown
            from rich.console import Group
            from rich.align import Align
            
            # Build content with reasoning if available
            content_parts = []
            if reasoning_text.strip():
                content_parts.append(f"[dim]💭 Reasoning:[/dim]\n{reasoning_text}\n")
            
            # Use Markdown for response_text
            markdown_content = Markdown(response_text)
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
            # Build message content (original logic for tool calls)
            message_parts = []
            if reasoning_text.strip():
                message_parts.append(f"[dim]💭 Reasoning:[/dim]\n{reasoning_text}")
            
            if response_text.strip():
                message_parts.append(response_text)
            
            # Add tool calls
            if tool_calls:
                tool_calls_formatted = self._format_tool_calls(tool_calls)
                if tool_calls_formatted:
                    message_parts.append(tool_calls_formatted)
            
            message_content = "\n\n".join(message_parts)
            
            # Render to console
            if message_content.strip():
                message_panel = Panel(
                    message_content.strip(),
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
        
        # Render based on content length
        content_length = len(result_content)
        max_preview_length = 500
        max_preview_lines = 20
        
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
    
    async def _create_workspace(self, session_id: str):
        """Create local workspace for the session.
        
        Args:
            session_id: Session ID for the workspace
            
        Returns:
            WorkSpace instance or None if WorkSpace is not available
        """
        if WorkSpace is None:
            return None
        
        # Create workspace in current directory under .aworld/workspaces
        workspace_base = Path.cwd() / ".aworld" / "workspaces"
        os.environ['WORKSPACE_PATH'] = str(workspace_base)
        workspace_base.mkdir(parents=True, exist_ok=True)
        
        # Create workspace storage path
        workspace_path = workspace_base / session_id
        
        # Create local workspace
        workspace = WorkSpace.from_local_storages(
            session_id=session_id,
            storage_path=str(workspace_path)
        )
        
        return workspace

__all__ = ["LocalAgentExecutor"]

