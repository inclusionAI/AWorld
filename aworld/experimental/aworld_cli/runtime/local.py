"""
Local runtime for CLI protocols.
Interacts with agents loaded from local directory.
"""
import os
from datetime import datetime
from typing import List, Optional
from aworld.core.context.amni import ApplicationContext, TaskInput
from aworld.core.context.amni.config import AmniConfigFactory
from .base import BaseAgentRuntime
from ..models import AgentInfo
from ..executors import AgentExecutor
from ..executors.local import LocalAgentExecutor
from ..core.registry import LocalAgentRegistry
from ..core.loader import init_agents


class LocalRuntime(BaseAgentRuntime):
    """
    Local runtime for AWorldApp using aworld-cli.
    Enables interacting with agents loaded from local directory.
    
    Example:
        >>> runtime = LocalRuntime()
        >>> await runtime.start()
    """
    
    def __init__(self, agent_name: Optional[str] = None):
        """
        Initialize Local Runtime.
        
        Args:
            agent_name: The name of the agent to interact with. If None, user will be prompted to select.
        """
        super().__init__(agent_name)
        self.local_agents_dir = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR")
    
    async def _load_agents(self) -> List[AgentInfo]:
        """Load agents from local directory."""
        # Load agents from local directory
        if self.local_agents_dir:
            self.cli.console.print(f"📂 Loading local agents from: {self.local_agents_dir}")
            init_agents(self.local_agents_dir)
        else:
            # Use current working directory as default
            default_dir = os.getcwd()
            self.cli.console.print(f"📂 LOCAL_AGENTS_DIR not set. Using current directory: {default_dir}")
            init_agents(default_dir)
            self.local_agents_dir = default_dir
        
        agents = LocalAgentRegistry.list_agents()
        if not agents:
            self.cli.console.print("[red]❌ No agents registered.[/red]")
            self.cli.console.print("Please configure LOCAL_AGENTS_DIR in .env or ensure agents are defined correctly.")
            return []
        
        return [AgentInfo.from_source(agent) for agent in agents]
    
    async def _create_executor(self, agent: AgentInfo) -> Optional[AgentExecutor]:
        """Create executor for local agent."""
        try:
            # Get the agent from registry
            local_agent = LocalAgentRegistry.get_agent(agent.name)
            if not local_agent:
                self.cli.console.print(f"[red]❌ Agent '{agent.name}' not found in registry.[/red]")
                return None
            
            # Get context config from agent if available
            context_config = local_agent.context_config if hasattr(local_agent, 'context_config') else AmniConfigFactory.create()
            
            # Try to get swarm without context first (for swarm instances or functions that don't need context)
            try:
                swarm = await local_agent.get_swarm(None)
            except (TypeError, AttributeError):
                # If swarm function requires context, create a temporary context
                # Create a temporary TaskInput for swarm initialization
                temp_task_input = TaskInput(
                    user_id="cli_user",
                    session_id=f"temp_session_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    task_id=f"temp_task_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    task_content="",
                    origin_user_input=""
                )
                # Create temporary context
                temp_context = await ApplicationContext.from_input(
                    temp_task_input,
                    context_config=context_config
                )
                # Get swarm with context
                swarm = await local_agent.get_swarm(temp_context)
            
            return LocalAgentExecutor(swarm, context_config=context_config, console=self.cli.console)
            
        except Exception as e:
            self.cli.console.print(f"[red]❌ Failed to initialize agent session: {e}[/red]")
            import traceback
            traceback.print_exc()
            return None
    
    def _get_source_type(self) -> str:
        """Get source type for display."""
        return "LOCAL"
    
    def _get_source_location(self) -> str:
        """Get source location for display."""
        return self.local_agents_dir or os.getcwd()

__all__ = ["LocalRuntime"]

