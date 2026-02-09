"""
Local agent executor.
"""
import os
import asyncio
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Union, Text
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.live import Live
from rich.syntax import Syntax
from rich.columns import Columns
from rich.align import Align

from aworld.config import TaskConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.common import Observation
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel
from aworld.core.task import Task
from aworld.runner import Runners
from .base_executor import BaseAgentExecutor
from .hooks import ExecutorHookPoint, ExecutorHook

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
        session_id: Optional[str] = None,
        hooks: Optional[List[str]] = None
    ):
        """
        Initialize local agent executor.
        
        Args:
            swarm: Swarm instance from agent team
            context_config: Context configuration for ApplicationContext. If None, will use default config.
            console: Optional Rich console for output
            session_id: Optional session ID. If None, will generate one automatically.
            hooks: Optional list of hook names (registered with HookFactory)

        Example:
            >>> executor = LocalAgentExecutor(swarm)
            >>> response = await executor.chat("Hello")
        """
        # Initialize base executor (handles session management, logging, etc.)
        super().__init__(console=console, session_id=session_id)

        # Local-specific initialization
        self.swarm = swarm
        self.context_config = context_config
        self._hooks_config = hooks or []
        self._hooks = self._load_hooks()

    def _load_hooks(self) -> Dict[str, List[ExecutorHook]]:
        """
        Load hooks from configuration.

        Hooks are provided as a list of hook names (registered with HookFactory).
        Each hook is retrieved by name, instantiated, and grouped by its hook point
        (returned by hook.point() method).

        FileParseHook is automatically registered as a default hook for file parsing.

        Returns:
            Dict mapping hook point to list of hook instances

        Example:
            >>> hooks = executor._load_hooks()
            >>> # Returns: {"post_input_parse": [FileParseHook()], "post_build_context": [ImageParseHook()], ...}
        """
        from aworld.runners.hook.hook_factory import HookFactory

        hooks = {}

        # Automatically register FileParseHook as default hook
        try:
            from .file_parse_hook import FileParseHook

            file_parse_hook = FileParseHook()
            hook_point = file_parse_hook.point()
            if hook_point not in hooks:
                hooks[hook_point] = []
            hooks[hook_point].append(file_parse_hook)

            # Silently register, no console output needed
        except Exception as e:
            if self.console:
                self.console.print(f"[yellow]⚠️ [Executor] Failed to auto-register FileParseHook: {e}[/yellow]")

        if not self._hooks_config:
            return hooks

        for hook_name in self._hooks_config:
            try:
                # Get hook class from HookFactory by name
                hook_cls = HookFactory.get_class(hook_name)
                if not hook_cls:
                    if self.console:
                        self.console.print(f"[yellow]⚠️ [Executor] Hook '{hook_name}' not found in HookFactory[/yellow]")
                    continue

                # Instantiate hook class
                hook_instance = hook_cls()

                # Get hook point from the instance
                hook_point = hook_instance.point()

                # Group hooks by their point
                if hook_point not in hooks:
                    hooks[hook_point] = []
                hooks[hook_point].append(hook_instance)

                if self.console:
                    self.console.print(f"[dim]✅ [Executor] Loaded hook '{hook_name}' for point '{hook_point}'[/dim]")
            except Exception as e:
                if self.console:
                    self.console.print(f"[red]❌ [Executor] Failed to load hook '{hook_name}': {e}[/red]")

        return hooks

    async def _execute_hooks(self, hook_point: str, **kwargs) -> Any:
        """
        Execute hooks for a specific hook point.

        This method follows the same pattern as runner hooks, using Message objects
        to pass parameters, but extracts results from message for executor use.

        After each hook execution, updates kwargs with any modified values from message.headers,
        so subsequent hooks and the caller can see the updates.

        Args:
            hook_point: Hook point name from ExecutorHookPoint
            **kwargs: Parameters to pass to hooks (will be in message.headers)

        Returns:
            Result extracted from message.payload or message.headers, or None if no hooks executed

        Example:
            >>> result = await executor._execute_hooks(
            ...     ExecutorHookPoint.POST_INPUT_PARSE,
            ...     user_message="test",
            ...     context=context,
            ...     image_urls=["data:image/png;base64,..."]
            ... )
        """
        from aworld.core.event.base import Message

        hooks = self._hooks.get(hook_point, [])
        if not hooks:
            return None

        # Extract context from kwargs if available
        context = kwargs.get('context')
        if not context:
            # Try to get from other kwargs
            for key, value in kwargs.items():
                if isinstance(value, ApplicationContext):
                    context = value
                    break

        result = None
        for hook in hooks:
            try:
                # Create Message object with current kwargs in headers
                # Use a copy to avoid modifying the original
                message_headers = dict(kwargs)
                # Pass console to hook so it can output messages
                if self.console:
                    message_headers['console'] = self.console
                message = Message(
                    category="executor_hook",
                    payload=kwargs.get('payload'),
                    sender="LocalAgentExecutor",
                    session_id=context.session_id if context and hasattr(context, 'session_id') else None,
                    headers=message_headers
                )

                # Execute hook
                result_message = await hook.exec(message, context)

                # Update kwargs with any modifications from message.headers
                # This ensures subsequent hooks and the caller see the updates
                if result_message and result_message.headers:
                    for key, value in result_message.headers.items():
                        if key in kwargs or key in ['context', 'task_input', 'task', 'user_message', 'task_content']:
                            kwargs[key] = value
                            # Update context variable if it's the context that was modified
                            if key == 'context' and isinstance(value, ApplicationContext):
                                context = value

                    # Extract result - prioritize specific keys, then payload
                    if 'context' in result_message.headers:
                        result = result_message.headers['context']
                    elif 'task_input' in result_message.headers:
                        result = result_message.headers['task_input']
                    elif 'task' in result_message.headers:
                        result = result_message.headers['task']
                    elif 'result' in result_message.headers:
                        result = result_message.headers['result']
                    elif result_message.payload and result_message.payload != kwargs.get('payload'):
                        result = result_message.payload

            except Exception as e:
                if self.console:
                    self.console.print(f"[red]❌ [Executor] Hook '{hook.__class__.__name__}' failed at '{hook_point}': {e}[/red]")

        return result

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
        
        # 🔥 Hook: PRE_INPUT_PARSE
        original_task_content = task_content
        hook_kwargs = {
            'user_message': task_content,
            'task_content': task_content,
            'image_urls': image_urls or [],
            'session_id': session_id,
            'task_id': task_id
        }
        hook_result = await self._execute_hooks(ExecutorHookPoint.PRE_INPUT_PARSE, **hook_kwargs)
        # Get updated task_content from kwargs
        task_content = hook_kwargs.get('task_content', task_content) or hook_kwargs.get('user_message', task_content)
        # Get updated image_urls from kwargs (FileParseHook may have added images)
        image_urls = hook_kwargs.get('image_urls', image_urls) or []

        # 1. Build task input
        task_input = TaskInput(
            user_id="user",
            session_id=session_id,
            task_id=task_id,
            task_content=task_content,
            origin_user_input=original_task_content
        )

        # 🔥 Hook: PRE_BUILD_CONTEXT
        hook_kwargs = {
            'task_input': task_input,
            'session_id': session_id,
            'task_id': task_id
        }
        hook_result = await self._execute_hooks(ExecutorHookPoint.PRE_BUILD_CONTEXT, **hook_kwargs)
        # Get updated task_input from kwargs
        task_input = hook_kwargs.get('task_input', task_input)

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
            _context.get_config().debug_mode=True
            await _context.init_swarm_state(_swarm)
            return _context
        
        context = await build_context(task_input, self.swarm, workspace)
        
        # 🔥 Hook: POST_BUILD_CONTEXT
        hook_kwargs = {
            'context': context,
            'task_input': task_input
        }
        hook_result = await self._execute_hooks(ExecutorHookPoint.POST_BUILD_CONTEXT, **hook_kwargs)
        # Get updated context from kwargs
        context = hook_kwargs.get('context', context)

        # 🔥 Hook: POST_INPUT_PARSE (after context is ready)
        # FileParseHook processes @filename references here
        hook_kwargs = {
            'task_input': task_input,
            'user_message': task_input.task_content,
            'context': context,
            'image_urls': image_urls or [],
            'original_input': original_task_content,
            'session_id': session_id,
            'task_id': task_id
        }
        hook_result = await self._execute_hooks(ExecutorHookPoint.POST_INPUT_PARSE, **hook_kwargs)
        # Get updated values from kwargs (FileParseHook may have modified them)
        context = hook_kwargs.get('context', context)
        task_input = hook_kwargs.get('task_input', task_input)
        image_urls = hook_kwargs.get('image_urls', image_urls) or []

        # 5. Build observation with images if provided
        # Use task_input.task_content (which may have been updated by FileParseHook) instead of old task_content
        observation = None
        if image_urls:
            observation = Observation(
                images=image_urls,
                content=task_input.task_content
            )

        # 🔥 Hook: PRE_BUILD_TASK
        hook_kwargs = {
            'task_input': task_input,
            'context': context,
            'swarm': self.swarm
        }
        hook_result = await self._execute_hooks(ExecutorHookPoint.PRE_BUILD_TASK, **hook_kwargs)
        # Get updated values from kwargs
        task_input = hook_kwargs.get('task_input', task_input)
        if 'task_content' in hook_kwargs:
            task_input.task_content = hook_kwargs['task_content']

        # 6. Build task with context and observation
        task = Task(
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

        # 🔥 Hook: POST_BUILD_TASK
        hook_kwargs = {
            'task': task
        }
        hook_result = await self._execute_hooks(ExecutorHookPoint.POST_BUILD_TASK, **hook_kwargs)
        # Get updated task from kwargs
        task = hook_kwargs.get('task', task)

        return task

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
            
            # 1. Ensure console is set - use global console if not set
            if not self.console:
                from .._globals import console as global_console
                self.console = global_console

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
            
            # 🔥 Hook: PRE_RUN_TASK
            hook_kwargs = {
                'task': task,
                'task_id': task.id,
                'session_id': task.session_id
            }
            hook_result = await self._execute_hooks(ExecutorHookPoint.PRE_RUN_TASK, **hook_kwargs)
            # Get updated task from kwargs
            task = hook_kwargs.get('task', task)

            # 4. Run task with streaming
            try:
                # Ensure console is set before running task
                # Use global console if self.console is not set
                if not self.console:
                    from .._globals import console as global_console
                    self.console = global_console

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
                                # Add indentation to elapsed time updates
                                indented_message = f"   {base_message} [{elapsed_str}]"
                                loading_status.update(f"[dim]{indented_message}[/dim]")
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
                        from aworld.output.base import MessageOutput, ToolResultOutput, StepOutput, TopologyOutput
                        
                        # Show loading status while waiting for first output
                        _start_loading_status("💭 Thinking...")
                        
                        # Track current agent for handoff detection
                        current_agent_name = None
                        last_agent_name = None

                        try:
                            # Ensure console is set before processing stream events
                            if not self.console:
                                from .._globals import console as global_console
                                self.console = global_console

                            async for output in outputs.stream_events():
                                if not self.console:
                                    continue

                                # Handle MessageOutput
                                if isinstance(output, MessageOutput):
                                    # Stop thinking status before rendering message
                                    _stop_loading_status()

                                    # Extract agent name from output metadata
                                    current_agent_name = None
                                    if hasattr(output, 'metadata') and output.metadata:
                                        current_agent_name = output.metadata.get('agent_name') or output.metadata.get('from_agent')

                                    # Fallback to get current agent from swarm
                                    if not current_agent_name and hasattr(self.swarm, 'cur_agent') and self.swarm.cur_agent:
                                        current_agent_name = getattr(self.swarm.cur_agent, 'name', None) or getattr(self.swarm.cur_agent, 'id', lambda: None)()

                                    # Default agent name
                                    if not current_agent_name:
                                        current_agent_name = "Assistant"

                                    # Check if this is a handoff (agent switch)
                                    is_handoff = last_agent_name is not None and last_agent_name != current_agent_name

                                    last_message_output = output
                                    # Pass agent_name and is_handoff parameters
                                    answer, _ = self._render_simple_message_output(output, answer, agent_name=current_agent_name, is_handoff=is_handoff)

                                    # Update last_agent_name for next iteration
                                    last_agent_name = current_agent_name
                                    
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
                                        # if has_non_human_tool:
                                            # Use dynamic loading status without icon prefix
                                            # _start_loading_status("Calling tool...")
                                    # If no tool calls, don't show thinking status here
                                    # It might be final response, or next output will trigger thinking status
                                
                                # Handle ToolResultOutput
                                elif isinstance(output, ToolResultOutput):
                                    # Stop "Calling tool..." status before rendering result
                                    # _stop_loading_status()
                                    
                                    # Render tool result
                                    self._render_simple_tool_result_output(output)
                                    
                                    # Immediately show thinking status after tool execution completes
                                    # Agent will process the tool result and think about next steps
                                    _start_loading_status("💭 Thinking...")
                                
                                # Handle StepOutput - don't interrupt Thinking status
                                elif isinstance(output, StepOutput):
                                    # StepOutput should not interrupt Thinking status
                                    # Just silently continue, keeping the Thinking status active
                                    # Optionally, we can log or render step info without stopping status
                                    pass

                                # Handle TopologyOutput
                                elif isinstance(output, TopologyOutput):
                                    # Stop any loading status
                                    _stop_loading_status()

                                    # Render topology
                                    self._render_topology_output(output)

                                    # Resume thinking status
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
                                            meta = getattr(output, "metadata", None) or {}
                                            print_all = meta.get("print_all", False)
                                            max_len = int(os.environ.get("AWORLD_CLI_MAX_RESULT_DISPLAY_LENGTH", "20000"))
                                            display_str = data_str if print_all else data_str[:max_len]
                                            if not print_all and len(data_str) > max_len:
                                                display_str += f"\n\n[dim]... ({len(data_str) - max_len} more characters) ...[/dim]"
                                            title = meta.get("title") or type(output).__name__
                                            generic_panel = Panel(
                                                display_str,
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
                
                # 🔥 Hook: POST_RUN_TASK
                hook_kwargs = {
                    'task': task,
                    'result': answer,
                    'task_id': task.id,
                    'session_id': task.session_id
                }
                hook_result = await self._execute_hooks(ExecutorHookPoint.POST_RUN_TASK, **hook_kwargs)
                # Get updated result from kwargs
                answer = hook_kwargs.get('result', answer)

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
                # 🔥 Hook: ON_TASK_ERROR
                try:
                    await self._execute_hooks(
                        ExecutorHookPoint.ON_TASK_ERROR,
                        error=err,
                        task_id=getattr(task, 'id', None) if 'task' in locals() else None,
                        session_id=self.session_id
                    )
                except Exception as hook_err:
                    # Don't let hook errors mask the original error
                    if self.console:
                        self.console.print(f"[yellow]⚠️ Hook error: {hook_err}[/yellow]")

                error_msg = f"Error: {err}, traceback: {traceback.format_exc()}"
                if self.console:
                    self.console.print(f"[red]❌ {error_msg}[/red]")
                raise
    
    # Note: _format_tool_call, _format_tool_calls, _render_message_output,
    # _render_tool_result_output, _extract_answer_from_output are now inherited from BaseAgentExecutor

    def _render_topology_output(self, output) -> None:
        """
        Render TopologyOutput to console with a cool box diagram.
        """
        from aworld.output.base import TopologyOutput
        from rich.table import Table
        from rich.box import ROUNDED
        # Import Group with fallback for older Rich versions
        try:
            from rich.console import Group
        except ImportError:
            try:
                from rich import Group
            except ImportError:
                # Fallback for older Rich versions
                class Group:
                    """Fallback Group class for older Rich versions."""
                    def __init__(self, *renderables):
                        self.renderables = renderables
                    
                    def __rich_console__(self, console, options):
                        for renderable in self.renderables:
                            yield renderable

        if not isinstance(output, TopologyOutput) or not self.console:
            return

        topology = output.topology
        team_name = output.team_name

        def get_agent_details(agent_name):
            """Find agent details by name."""
            # Try getting from output first (source of truth for this topology render)
            if hasattr(output, 'agent_details') and output.agent_details:
                return output.agent_details.get(agent_name)

            # Fallback to local swarm check (legacy/direct path)
            if not hasattr(self, 'swarm') or not self.swarm:
                return None

            # Try agent_graph first (O(1) lookup)
            if hasattr(self.swarm, 'agent_graph') and self.swarm.agent_graph:
                if hasattr(self.swarm.agent_graph, 'agents') and isinstance(self.swarm.agent_graph.agents, dict):
                    agent = self.swarm.agent_graph.agents.get(agent_name)
                    if agent:
                        # Normalize to dict format to match output.agent_details
                        details = {
                            "type": type(agent).__name__,
                            "tools": getattr(agent, 'tool_names', []),
                            "skills": list(getattr(agent, 'skill_configs', {}).keys()) if isinstance(getattr(agent, 'skill_configs', {}), dict) else []
                        }
                        return details

            return None

        def create_node_panel(item):
            if isinstance(item, dict) and "name" in item:
                name = item.get("name", "Unknown")
                node_type = item.get("type", "Agent")

                # Get detailed agent info
                agent_details = get_agent_details(name)

                # Build content
                content_parts = []
                content_parts.append(f"[bold white]{name}[/bold white]")
                content_parts.append(f"[dim]{node_type}[/dim]")

                if agent_details:
                    # Extract Skills
                    skills = agent_details.get("skills", [])
                    if skills:
                        content_parts.append("\n[bold cyan]📚 Skills:[/bold cyan]")
                        for skill in skills:
                            content_parts.append(f"[cyan]• {skill}[/cyan]")

                    # Extract Tools
                    tools = agent_details.get("tools", [])
                    if tools:
                        content_parts.append("\n[bold yellow]🛠️ Tools:[/bold yellow]")
                        for tool in tools:
                            # Handle if tool is object or string
                            tool_name = getattr(tool, 'name', str(tool)) if not isinstance(tool, str) else tool
                            content_parts.append(f"[yellow]• {tool_name}[/yellow]")

                    # Extract MCP Servers
                    mcp_servers = agent_details.get("mcp_servers", [])
                    if mcp_servers:
                        content_parts.append("\n[bold magenta]🔌 MCP Servers:[/bold magenta]")
                        for mcp in mcp_servers:
                            content_parts.append(f"[magenta]• {mcp}[/magenta]")

                    # Extract System Prompt (if available and not too long)
                    sys_prompt = agent_details.get("system_prompt", "")
                    if sys_prompt:
                        content_parts.append("\n[bold green]📝 Prompt:[/bold green]")
                        # Truncate if too long
                        if len(sys_prompt) > 100:
                            content_parts.append(f"[green]{sys_prompt[:100]}...[/green]")
                        else:
                            content_parts.append(f"[green]{sys_prompt}[/green]")

                final_content = "\n".join(content_parts)

                return Panel(
                    Align.center(final_content),
                    border_style="cyan",
                    padding=(1, 2),
                    expand=False,
                    width=50,
                    title="[bold cyan]Agent[/bold cyan]",
                    title_align="center"
                )
            elif isinstance(item, str):
                return Panel(
                    Align.center(f"[bold white]{item}[/bold white]"),
                    border_style="cyan",
                    padding=(1, 2),
                    expand=False,
                    width=50
                )
            else:
                return Panel(
                    Align.center(f"[red]Unknown: {str(item)}[/red]"),
                    border_style="red",
                    expand=False,
                    width=50
                )

        def render_structure(item):
            # Workflow (List)
            if isinstance(item, list):
                children = [render_structure(sub) for sub in item]
                if not children:
                    return Text("Empty Workflow", style="dim")

                # Render top to bottom with dashed lines
                rows = []
                for i, child in enumerate(children):
                    rows.append(child)
                    if i < len(children) - 1:
                        # Downward thick pillar with arrow below
                        rows.append(Align.center(Text("⬇", style="bold yellow")))

                return Group(*rows)

            # Serial Group (Tuple)
            elif isinstance(item, tuple):
                children = [render_structure(sub) for sub in item]
                if not children:
                    return Text("Empty Sequence", style="dim")

                # Render top to bottom with dashed lines
                rows = []
                for i, child in enumerate(children):
                    rows.append(child)
                    if i < len(children) - 1:
                        # Downward thick pillar with arrow below
                        rows.append(Align.center(Text("⬇", style="bold yellow")))

                return Panel(
                    Group(*rows),
                    title="[bold magenta]🔄 Serial Execution[/bold magenta]",
                    border_style="magenta",
                    padding=(1, 2),
                    title_align="center"
                )

            # Leaf Node
            else:
                return create_node_panel(item)

        # Main rendering logic
        if isinstance(topology, (list, tuple)):
            # If top level is list/tuple, treat it as such
            content = render_structure(topology)
        else:
            # Single item
            content = render_structure(topology)

        # Wrap everything in a main panel
        main_panel = Panel(
            Align.center(content),
            title=f"[bold green]🤖 Team Topology: {team_name}[/bold green]",
            border_style="green",
            padding=(1, 2),
            expand=True
        )

        self.console.print(main_panel)
        self.console.print()

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

