"""
Local agent executor.
"""
import os
import sys
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from rich.console import Console

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
        console: Optional[Console] = None
    ):
        """
        Initialize local agent executor.
        
        Args:
            swarm: Swarm instance from agent team
            context_config: Context configuration for ApplicationContext. If None, will use default config.
            console: Optional Rich console for output
            
        Example:
            >>> executor = LocalAgentExecutor(swarm)
            >>> response = await executor.chat("Hello")
        """
        self.swarm = swarm
        self.context_config = context_config
        self.console = console
    
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
            session_id: Optional session ID. If None, will generate one.
            task_id: Optional task ID. If None, will generate one.
            
        Returns:
            Task instance
        """
        if not session_id:
            session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
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
            
            # 2. Build task
            task = await self._build_task(message)
            
            # 3. Run task
            try:
                if self.console:
                    self.console.print(f"[dim]🔄 Running task: {task.id}[/dim]")
                
                result = await Runners.run_task(task=task)
                
                if self.console:
                    self.console.print(f"[dim]📦 Task result received: {type(result)}[/dim]")
                
                # Extract answer from result
                answer = ""
                if result and isinstance(result, dict):
                    if task.id in result:
                        task_response = result[task.id]
                        if self.console:
                            self.console.print(f"[dim]📋 TaskResponse type: {type(task_response)}[/dim]")
                        
                        # Try different ways to get the answer
                        if hasattr(task_response, 'answer'):
                            answer = task_response.answer
                            if self.console:
                                self.console.print(f"[dim]✅ Got answer from .answer attribute[/dim]")
                        elif isinstance(task_response, dict):
                            answer = task_response.get('answer', '')
                            if self.console:
                                self.console.print(f"[dim]✅ Got answer from dict[/dim]")
                        else:
                            answer = str(task_response) if task_response else ""
                            if self.console:
                                self.console.print(f"[dim]✅ Got answer from str conversion[/dim]")
                    else:
                        if self.console:
                            self.console.print(f"[yellow]⚠️ Task ID '{task.id}' not found in result[/yellow]")
                            self.console.print(f"[dim]Available keys: {list(result.keys())}[/dim]")
                elif result:
                    if self.console:
                        self.console.print(f"[yellow]⚠️ Result is not a dict: {type(result)}[/yellow]")
                else:
                    if self.console:
                        self.console.print(f"[yellow]⚠️ Result is None or empty[/yellow]")
                
                # Print result if available
                if answer:
                    if self.console:
                        self.console.print(f"[green]✅ Task completed[/green]")
                    self.console.print(f"{answer}")
                else:
                    if self.console:
                        self.console.print(f"[yellow]⚠️ Task completed but answer is empty[/yellow]")
                        if result:
                            self.console.print(f"[dim]Debug info:[/dim]")
                            self.console.print(f"[dim]  - Result type: {type(result)}[/dim]")
                            if isinstance(result, dict):
                                self.console.print(f"[dim]  - Result keys: {list(result.keys())}[/dim]")
                                if task.id in result:
                                    task_response = result[task.id]
                                    self.console.print(f"[dim]  - TaskResponse type: {type(task_response)}[/dim]")
                                    if hasattr(task_response, '__dict__'):
                                        self.console.print(f"[dim]  - TaskResponse attrs: {list(task_response.__dict__.keys())}[/dim]")

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

