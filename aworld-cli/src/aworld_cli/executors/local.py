"""
Local agent executor.
"""
import asyncio
import os
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

from dotenv import load_dotenv
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.status import Status
from rich.text import Text
from aworld.logs.util import logger

from aworld.config import TaskConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel
from aworld.core.task import Task
from aworld.runner import Runners
from .base_executor import BaseAgentExecutor
from .hooks import ExecutorHookPoint, ExecutorHook
from .stats import StreamTokenStats, format_elapsed

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

    async def cleanup_resources(self) -> None:
        """
        Close MCP and other resources in the same event loop to avoid
        "Attempted to exit cancel scope in a different task" on exit.
        """
        agents = []
        if getattr(self, "swarm", None):
            if hasattr(self.swarm, "agent_graph") and self.swarm.agent_graph and hasattr(self.swarm.agent_graph, "agents"):
                agents = list(self.swarm.agent_graph.agents.values())
            if not agents and hasattr(self.swarm, "agents"):
                ag = self.swarm.agents
                agents = list(ag.values()) if isinstance(ag, dict) else list(ag) if ag else []
        seen_sandboxes = set()
        for agent in agents:
            # 1. Cleanup sandbox.mcpservers (MCP connections) - dedupe by id
            sandbox = getattr(agent, "sandbox", None)
            if sandbox is not None and id(sandbox) not in seen_sandboxes:
                seen_sandboxes.add(id(sandbox))
                mcpservers = getattr(sandbox, "mcpservers", None) or getattr(sandbox, "_mcpservers", None)
                if mcpservers is not None and hasattr(mcpservers, "cleanup") and callable(mcpservers.cleanup):
                    try:
                        await mcpservers.cleanup()
                    except Exception as e:
                        logger.warning(f"MCP cleanup on exit: {e}")
                elif hasattr(sandbox, "cleanup") and callable(sandbox.cleanup):
                    try:
                        await sandbox.cleanup()
                    except Exception as e:
                        logger.warning(f"Sandbox cleanup on exit: {e}")
            # 2. Cleanup tool.action_executor (legacy path)
            for tool in getattr(agent, "tools", []) or []:
                action_exec = getattr(tool, "action_executor", None)
                if action_exec is not None and hasattr(action_exec, "cleanup") and callable(getattr(action_exec, "cleanup")):
                    try:
                        await action_exec.cleanup()
                    except Exception as e:
                        logger.warning(f"MCP cleanup on exit: {e}")

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
                    streaming_mode = False
                    stream_token_stats = StreamTokenStats()
                    accumulated_stream_content = ""
                    accumulated_tool_calls = []
                    stream_live = None
                    _last_stream_update = 0.0

                    def _render_stream_display():
                        """Build combined renderable: stats line, agent name, content, tool_calls (refreshed together)."""
                        nonlocal accumulated_stream_content, accumulated_tool_calls, stream_token_stats, status_start_time
                        parts = [Text("")]
                        elapsed_str = format_elapsed((datetime.now() - status_start_time).total_seconds()) if status_start_time else "0.0s"
                        msg = stream_token_stats.format_streaming_line(elapsed_str)
                        if msg:
                            parts.append(Text.from_markup(msg))
                        stats = stream_token_stats.get_current_stats()
                        aname = (stats or {}).get("agent_name") or "Assistant"
                        if msg or accumulated_stream_content or accumulated_tool_calls:
                            parts.append(Text.from_markup(f"🤖 [bold cyan]{aname}[/bold cyan]"))
                        if accumulated_stream_content:
                            content = accumulated_stream_content.strip("\n")
                            content = re.sub(r"\n{2,}", "\n", content)  # collapse multiple newlines to one
                            indented = "\n".join("   " + line for line in content.split("\n"))
                            parts.append(Text(indented))
                            # if accumulated_tool_calls:
                            #     parts.append(Text(""))
                        if accumulated_tool_calls:
                            tool_lines = self._format_tool_calls_display_lines(accumulated_tool_calls)
                            if tool_lines:
                                parts.append(Text.from_markup("🔧 [bold]Tool calls[/bold]"))
                                tool_str = "\n".join(f"   {line}" if line else "" for line in tool_lines).rstrip("\n")
                                if tool_str:
                                    parts.append(Text.from_markup(tool_str))
                        return Group(*parts) if parts else Text("")

                    async def _update_elapsed_time():
                        """Update elapsed time in status message. Shows token stats when streaming."""
                        nonlocal loading_status, status_start_time, base_message, streaming_mode, stream_live
                        while (loading_status or stream_live) and status_start_time:
                            elapsed = (datetime.now() - status_start_time).total_seconds()
                            elapsed_str = format_elapsed(elapsed)
                            if stream_live:
                                stream_live.update(_render_stream_display())
                            elif loading_status:
                                if streaming_mode:
                                    msg = stream_token_stats.format_streaming_line(elapsed_str)
                                    if msg:
                                        loading_status.update(f"[dim]{msg}[/dim]")
                                    else:
                                        loading_status.update(f"[dim]   {base_message} [{elapsed_str}][/dim]")
                                else:
                                    loading_status.update(f"[dim]   {base_message} [{elapsed_str}][/dim]")
                            await asyncio.sleep(0.15)  # Update every 0.15s for smoother stats display
                    
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
                        """Stop loading status and stream live display."""
                        nonlocal loading_status, status_start_time, status_update_task, stream_live
                        if status_update_task:
                            status_update_task.cancel()
                            status_update_task = None
                        if stream_live:
                            stream_live.stop()
                            stream_live = None
                        if loading_status:
                            loading_status.stop()
                            loading_status = None
                        status_start_time = None
                    
                    try:
                        from aworld.output.base import MessageOutput, ToolResultOutput, StepOutput, ChunkOutput
                        
                        # Show loading status while waiting for first output
                        logger.info(f"Start thinking status: {loading_status} {status_start_time}")
                        _start_loading_status("💭 Thinking...")
                        await asyncio.sleep(0)  # Yield so _update_elapsed_time task can start

                        # Track current agent for handoff detection
                        current_agent_name = None
                        last_agent_name = None
                        received_chunk_output = False

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
                                    elapsed_sec = (datetime.now() - status_start_time).total_seconds() if status_start_time else None
                                    _stop_loading_status()

                                    stream_on = os.environ.get("STREAM", "0").lower() in ("1", "true", "yes")
                                    if stream_on:
                                        if received_chunk_output and stream_token_stats.get_current_stats():
                                            # stream_token_stats.show_final(self.console, elapsed_sec=elapsed_sec)
                                            stream_token_stats.clear()
                                            accumulated_stream_content = ""
                                            accumulated_tool_calls = []
                                    # Extract agent name from output metadata
                                    current_agent_name = None
                                    if hasattr(output, 'metadata') and output.metadata:
                                        current_agent_name = output.metadata.get('agent_name') or output.metadata.get('from_agent')

                                    # Fallback to get current agent from swarm
                                    if not current_agent_name and hasattr(self.swarm, 'cur_agent') and self.swarm.cur_agent:
                                        current_agent_name = getattr(self.swarm.cur_agent, 'name', None) or getattr(self.swarm.cur_agent, 'id', lambda: None)()
                                    logger.info(f"Stop thinking status: {loading_status} {status_start_time} {elapsed_sec} {current_agent_name} {last_agent_name} {received_chunk_output} {stream_token_stats.get_current_stats()} {accumulated_stream_content} {accumulated_tool_calls}")

                                    # Default agent name
                                    if not current_agent_name:
                                        current_agent_name = "Assistant"

                                    # Check if this is a handoff (agent switch)
                                    is_handoff = last_agent_name is not None and last_agent_name != current_agent_name

                                    last_message_output = output
                                    # When STREAM=1: render message output; when STREAM=0: skip output, only update answer
                                    if not stream_on:
                                        logger.info(f"Rendering message output for agent: {current_agent_name}")
                                        logger.info(f"Output: {output}")
                                        logger.info(f"Answer: {answer}")
                                        logger.info(f"Is handoff: {is_handoff}")
                                        answer, _ = self._render_simple_message_output(output, answer, agent_name=current_agent_name, is_handoff=is_handoff, content_already_streamed=received_chunk_output)
                                    else:
                                        response_text = str(output.response) if hasattr(output, 'response') and output.response else ""
                                        if response_text.strip():
                                            answer = response_text if not answer else (response_text if response_text not in answer else answer)

                                    # Update last_agent_name for next iteration
                                    last_agent_name = current_agent_name
                                    
                                    # Check if there are tool calls - if so, show "Thinking..." for agent-as-tool
                                    # Skip status for human tools as they require user interaction
                                    tool_calls = output.tool_calls if hasattr(output, 'tool_calls') and output.tool_calls else []
                                    if tool_calls:
                                        from aworld.models.model_response import ToolCall
                                        from aworld.core.agent.base import is_agent_by_name
                                        has_agent_as_tool = False
                                        for tool_call_output in tool_calls:
                                            tool_call = None
                                            if hasattr(tool_call_output, 'data'):
                                                tool_call = tool_call_output.data
                                            elif isinstance(tool_call_output, ToolCall):
                                                tool_call = tool_call_output
                                            else:
                                                tool_call = tool_call_output
                                            if tool_call:
                                                function_name = ""
                                                if hasattr(tool_call, 'function') and tool_call.function:
                                                    function_name = getattr(tool_call.function, 'name', '')
                                                if 'human' not in function_name.lower() and is_agent_by_name(function_name):
                                                    has_agent_as_tool = True
                                                    break
                                        if has_agent_as_tool:
                                            _start_loading_status("💭 Thinking...")
                                    elif not tool_calls and (current_agent_name or "").lower() != "aworld":
                                        # No tool calls and not Aworld: agent may produce more output
                                        _start_loading_status("💭 Thinking...")
                                
                                # Handle ToolResultOutput
                                elif isinstance(output, ToolResultOutput):
                                    # Stop "Calling tool..." status before rendering result
                                    # _stop_loading_status()
                                    
                                    # Render tool result
                                    self._render_simple_tool_result_output(output)
                                    
                                    # Immediately show thinking status after tool execution completes
                                    # Agent will process the tool result and think about next steps
                                    logger.info(f"Start thinking status: {loading_status} {status_start_time} {elapsed_sec} {current_agent_name} {last_agent_name} {received_chunk_output} {stream_token_stats.get_current_stats()} {accumulated_stream_content} {accumulated_tool_calls}")
                                    _start_loading_status("💭 Thinking...")
                                
                                # Handle StepOutput - don't interrupt Thinking status
                                elif isinstance(output, StepOutput):
                                    # StepOutput should not interrupt Thinking status
                                    # Just silently continue, keeping the Thinking status active
                                    # Optionally, we can log or render step info without stopping status
                                    pass

                                # Handle ChunkOutput - accumulate token and tool_calls stats, refresh display in real-time
                                elif isinstance(output, ChunkOutput):
                                    received_chunk_output = True
                                    streaming_mode = True
                                    stream_on = os.environ.get("STREAM", "0").lower() in ("1", "true", "yes")
                                    chunk = output.data if hasattr(output, "data") else getattr(output, "data", None)
                                    if stream_on and chunk:
                                        if content := getattr(chunk, "content", None):
                                            accumulated_stream_content += content
                                        if tool_calls := getattr(chunk, "tool_calls", None):
                                            accumulated_tool_calls = list(tool_calls)
                                    meta = getattr(output, "metadata", None) or {}
                                    out_tok = meta.get("output_tokens")
                                    inp_tok = meta.get("input_tokens")
                                    tc_count = meta.get("tool_calls_count")
                                    tc_content_len = meta.get("tool_calls_content_length")
                                    out_est = meta.get("output_tokens_estimated", False)
                                    inp_est = meta.get("input_tokens_estimated", False)
                                    tc_est = meta.get("tool_calls_count_estimated", False)
                                    tc_content_est = meta.get("tool_calls_content_estimated", False)
                                    agent_id = meta.get("agent_id")
                                    agent_name = meta.get("agent_name")
                                    if out_tok is None or inp_tok is None or tc_count is None:
                                        chunk = output.data if hasattr(output, "data") else getattr(output, "data", None)
                                        if chunk:
                                            u = getattr(chunk, "usage", None) or {}
                                            if out_tok is None:
                                                out_tok = u.get("completion_tokens")
                                                if out_tok is None or out_tok == 0:
                                                    content = getattr(chunk, "content", None) or ""
                                                    out_tok = max(0, len(content) // 4)
                                                    out_est = True
                                            if inp_tok is None:
                                                inp_tok = u.get("prompt_tokens")
                                                if inp_tok is None or inp_tok == 0:
                                                    inp_est = True
                                            if tc_count is None:
                                                tc_count = len(getattr(chunk, "tool_calls", None) or [])
                                                tc_est = True
                                            if tc_content_len is None and getattr(chunk, "tool_calls", None):
                                                tc_content_len = sum(
                                                    len(getattr(getattr(tc, "function", None), "arguments", None) or "")
                                                    for tc in chunk.tool_calls
                                                )
                                                tc_content_est = True
                                    if agent_id is None or agent_name is None:
                                        if hasattr(self.swarm, "cur_agent") and self.swarm.cur_agent:
                                            agent_id = agent_id or getattr(self.swarm.cur_agent, "id", lambda: None)()
                                            agent_name = agent_name or getattr(self.swarm.cur_agent, "name", None)
                                    if out_tok is not None or inp_tok is not None or tc_count is not None:
                                        stream_token_stats.update(
                                            agent_id, agent_name,
                                            out_tok if out_tok is not None else 0,
                                            inp_tok,
                                            tc_count if tc_count is not None else 0,
                                            output_estimated=out_est,
                                            input_estimated=inp_est,
                                            tool_calls_estimated=tc_est,
                                            tool_calls_content_length=tc_content_len,
                                            tool_calls_content_estimated=tc_content_est,
                                            tool_calls=accumulated_tool_calls if accumulated_tool_calls else None,
                                            content=accumulated_stream_content if accumulated_stream_content else None,
                                        )
                                    # When STREAM=1: use Live to show content + stats + tool_calls together (no separate print)
                                    if stream_on and self.console and (accumulated_stream_content or accumulated_tool_calls or stream_token_stats.get_current_stats()):
                                        if stream_live is None:
                                            if loading_status:
                                                loading_status.stop()
                                                loading_status = None
                                            stream_live = Live(console=self.console, refresh_per_second=10)
                                            stream_live.start()
                                        # Throttle updates to ~15/sec for smoother display
                                        now = time.monotonic()
                                        if now - _last_stream_update >= 0.07:
                                            _last_stream_update = now
                                            stream_live.update(_render_stream_display())

                                # Handle other output types
                                else:
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
                            pass
                    
                    except Exception as e:
                        _stop_loading_status()
                        if self.console:
                            error_body = Text("Error in stream consumption: ")
                            error_body.append(str(e))
                            error_panel = Panel(
                                error_body,
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
                        logger.error(f"console|TimeoutError: Task still running, using streamed answer")
                        # if self.console:
                        #     self.console.print(f"[dim]ℹ️ Task still running, using streamed answer[/dim]")
                    except Exception as e:
                        logger.error(f"console|Exception: Error waiting for final result: {e}")
                        # if self.console:
                        #     self.console.print(f"[yellow]⚠️ Error waiting for final result: {e}[/yellow]")
                
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
                    self.console.print("[red]❌ [/red]", end=" ")
                    self.console.print(error_msg, markup=False)
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

