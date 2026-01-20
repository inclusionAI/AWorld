"""
Local agent executor.
"""
import os
import asyncio
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.status import Status

from aworld.config import TaskConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.common import Observation
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel
from aworld.core.task import Task
from aworld.runner import Runners
from .base_executor import BaseAgentExecutor

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


class LocalAgentExecutor(BaseAgentExecutor):
    """
    Executor for local agents.
    
    Only responsible for:
    - Building Task objects
    - Executing tasks locally
    - Workspace management (local-specific)
    - Skill status query (local-specific)
    
    All other capabilities (session management, output rendering, logging) are inherited from BaseAgentExecutor.
    """
    
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
        # Initialize base executor (handles session management, logging, etc.)
        super().__init__(console=console, session_id=session_id)
        
        # Local-specific initialization
        self.swarm = swarm
        self.context_config = context_config
    
    async def _build_task(
        self, 
        task_content: str, 
        session_id: str = None, 
        task_id: str = None,
        image_urls: Optional[List[str]] = None
    ) -> Task:
        """
        Build task from task content.
        
        Args:
            task_content: Task content string
            session_id: Optional session ID. If None, will use the executor's current session_id.
            task_id: Optional task ID. If None, will generate one.
            image_urls: Optional list of image data URLs (base64 encoded) for multimodal support
            
        Returns:
            Task instance
            
        Example:
            >>> # Text only
            >>> task = await executor._build_task("Hello")
            >>> # With images
            >>> task = await executor._build_task("Analyze this", image_urls=["data:image/jpeg;base64,..."])
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
        
        # 5. Build observation with images if provided
        observation = None
        if image_urls:
            observation = Observation(
                images=image_urls,
                content=task_content
            )
        
        # 6. Build task with context and observation
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
            timeout=60 * 60,
            observation=observation
        )
    
    async def chat(self, message: Union[str, tuple[str, List[str]]]) -> str:
            """
            Execute chat with local agent using Task/Runners pattern.
            
            Args:
                message: User message (string) or tuple of (text, image_urls) for multimodal support
                
            Returns:
                Agent response
                
            Example:
                >>> executor = LocalAgentExecutor(swarm)
                >>> # Text only
                >>> response = await executor.chat("Hello")
                >>> # With images
                >>> response = await executor.chat(("Analyze this", ["data:image/jpeg;base64,..."]))
            """
            # 0. Init middlewares (logging is already set up in base __init__)
            load_dotenv()
            init_middlewares()
            
            # 2. Parse message - handle both string and tuple format
            if isinstance(message, tuple):
                task_content, image_urls = message
            else:
                task_content = message
                image_urls = None
            
            # 3. Build task (will use current session_id)
            # Update session last used time
            self._update_session_last_used(self.session_id)
            task = await self._build_task(task_content, session_id=self.session_id, image_urls=image_urls)
            
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
                    status_start_time = None
                    status_update_task = None
                    base_message = ""
                    
                    async def _update_elapsed_time():
                        """Update elapsed time in status message."""
                        nonlocal loading_status, status_start_time, base_message
                        while loading_status and status_start_time:
                            elapsed = (datetime.now() - status_start_time).total_seconds()
                            if elapsed < 60:
                                elapsed_str = f"{elapsed:.1f}s"
                            elif elapsed < 3600:
                                elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
                            else:
                                hours = int(elapsed // 3600)
                                minutes = int((elapsed % 3600) // 60)
                                elapsed_str = f"{hours}h {minutes}m"
                            
                            if loading_status:
                                loading_status.update(f"[dim]{base_message} [{elapsed_str}][/dim]")
                            await asyncio.sleep(0.5)  # Update every 0.5 seconds
                    
                    def _start_loading_status(message: str):
                        """Start or update loading status."""
                        nonlocal loading_status, status_start_time, status_update_task, base_message
                        if not self.console:
                            return
                        
                        base_message = message
                        status_start_time = datetime.now()
                        
                        # Add elapsed time for Thinking and Calling tool messages
                        if "Thinking" in message or "Calling tool" in message:
                            message_with_time = f"{message} [0.0s]"
                        else:
                            message_with_time = message
                        
                        if loading_status:
                            loading_status.update(f"[dim]{message_with_time}[/dim]")
                        else:
                            loading_status = Status(f"[dim]{message_with_time}[/dim]", console=self.console)
                            loading_status.start()
                        
                        # Start async task to update elapsed time
                        if ("Thinking" in message or "Calling tool" in message) and status_update_task is None:
                            status_update_task = asyncio.create_task(_update_elapsed_time())
                    
                    def _stop_loading_status():
                        """Stop loading status."""
                        nonlocal loading_status, status_start_time, status_update_task
                        if status_update_task:
                            status_update_task.cancel()
                            status_update_task = None
                        if loading_status:
                            loading_status.stop()
                            loading_status = None
                        status_start_time = None
                    
                    try:
                        from aworld.output.base import MessageOutput, ToolResultOutput, StepOutput
                        
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
                                    # Skip status for human tools as they require user interaction
                                    tool_calls = output.tool_calls if hasattr(output, 'tool_calls') and output.tool_calls else []
                                    if tool_calls:
                                        # Check if any tool is a human tool (requires user interaction, no loading status)
                                        # Use the same logic as _format_tool_calls to ensure consistency
                                        from aworld.models.model_response import ToolCall
                                        has_human_tool = False
                                        has_non_human_tool = False
                                        for tool_call_output in tool_calls:
                                            tool_call = None
                                            if hasattr(tool_call_output, 'data'):
                                                tool_call = tool_call_output.data
                                            elif isinstance(tool_call_output, ToolCall):
                                                tool_call = tool_call_output
                                            else:
                                                # Try to use it directly if it's already a ToolCall
                                                tool_call = tool_call_output
                                            
                                            if tool_call:
                                                function_name = ""
                                                if hasattr(tool_call, 'function') and tool_call.function:
                                                    function_name = getattr(tool_call.function, 'name', '')
                                                if 'human' in function_name.lower():
                                                    has_human_tool = True
                                                else:
                                                    has_non_human_tool = True
                                        
                                        # Only show loading status if there are non-human tools
                                        if has_non_human_tool:
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
                                
                                # Handle StepOutput - don't interrupt Thinking status
                                elif isinstance(output, StepOutput):
                                    # StepOutput should not interrupt Thinking status
                                    # Just silently continue, keeping the Thinking status active
                                    # Optionally, we can log or render step info without stopping status
                                    pass
                                
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
                                            idx = -1 if output.metadata.get("print_all", False) else 500
                                            title = output.metadata.get("title")
                                            title = title if title else type(output).__name__
                                            generic_panel = Panel(
                                                data_str[:idx],
                                                title=f"[dim]📦 {title}[/dim]",
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
                        _stop_loading_status()
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
    
    # Note: _format_tool_call, _format_tool_calls, _render_message_output, 
    # _render_tool_result_output, _extract_answer_from_output are now inherited from BaseAgentExecutor
    
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
    
    def get_skill_status(self) -> Dict[str, Any]:
        """
        Get skill status from swarm agents.
        
        Returns:
            Dictionary with 'total', 'active', 'inactive' counts, and 'active_names' list
            
        Example:
            >>> executor = LocalAgentExecutor(swarm)
            >>> status = executor.get_skill_status()
            >>> print(f"Total: {status['total']}, Active: {status['active']}, Inactive: {status['inactive']}")
            >>> print(f"Active skills: {status['active_names']}")
        """
        total = 0
        active = 0
        inactive = 0
        active_names = []
        
        try:
            # Collect all skills from all agents in swarm
            all_skills = {}
            
            # Try multiple ways to get agents from swarm
            agents_to_check = []
            
            # Method 1: Try agent_graph.agents (most reliable after initialization)
            if hasattr(self.swarm, 'agent_graph') and self.swarm.agent_graph:
                if hasattr(self.swarm.agent_graph, 'agents') and self.swarm.agent_graph.agents:
                    if isinstance(self.swarm.agent_graph.agents, dict):
                        agents_to_check.extend(self.swarm.agent_graph.agents.values())
                    elif isinstance(self.swarm.agent_graph.agents, (list, tuple)):
                        agents_to_check.extend(self.swarm.agent_graph.agents)
            
            # Method 2: Try swarm.agents (direct access)
            if not agents_to_check and hasattr(self.swarm, 'agents') and self.swarm.agents:
                if isinstance(self.swarm.agents, dict):
                    agents_to_check.extend(self.swarm.agents.values())
                elif isinstance(self.swarm.agents, (list, tuple)):
                    agents_to_check.extend(self.swarm.agents)
                else:
                    agents_to_check.append(self.swarm.agents)
            
            # Method 3: Try _communicate_agent (root agent)
            if not agents_to_check and hasattr(self.swarm, '_communicate_agent'):
                communicate_agent = self.swarm._communicate_agent
                if communicate_agent:
                    if isinstance(communicate_agent, list):
                        agents_to_check.extend(communicate_agent)
                    else:
                        agents_to_check.append(communicate_agent)
            
            # Method 4: Try topology (initial agents)
            if not agents_to_check and hasattr(self.swarm, 'topology') and self.swarm.topology:
                for item in self.swarm.topology:
                    if hasattr(item, 'skill_configs'):
                        agents_to_check.append(item)
                    elif isinstance(item, (list, tuple)):
                        agents_to_check.extend([a for a in item if hasattr(a, 'skill_configs')])
            
            # Collect skills from all found agents
            for agent in agents_to_check:
                if hasattr(agent, 'skill_configs') and agent.skill_configs:
                    if isinstance(agent.skill_configs, dict):
                        all_skills.update(agent.skill_configs)
            
            total = len(all_skills)
            
            # Count active and inactive skills, collect active names
            for skill_name, skill_config in all_skills.items():
                if isinstance(skill_config, dict):
                    # Check if skill is marked as active in config
                    if skill_config.get('active', False):
                        active += 1
                        active_names.append(skill_name)
                    else:
                        inactive += 1
                else:
                    # If skill_config is not a dict, count as inactive
                    inactive += 1
                    
        except Exception:
            # If any error occurs, return zeros
            # Don't print error to avoid cluttering startup message
            pass
        
        return {
            'total': total,
            'active': active,
            'inactive': inactive,
            'active_names': active_names
        }

__all__ = ["LocalAgentExecutor"]

