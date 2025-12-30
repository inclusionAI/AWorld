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
from ..core.agent_registry import LocalAgentRegistry
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
        # Get the inner_plugins directory path (relative to this file)
        import pathlib
        current_dir = pathlib.Path(__file__).parent.parent
        inner_plugins_dir = current_dir / "inner_plugins"
        
        # Scan inner_plugins for agents directories (e.g., inner_plugins/*/agents)
        self.inner_plugins_agent_dirs = []
        if inner_plugins_dir.exists() and inner_plugins_dir.is_dir():
            for plugin_dir in inner_plugins_dir.iterdir():
                if plugin_dir.is_dir():
                    agents_dir = plugin_dir / "agents"
                    if agents_dir.exists() and agents_dir.is_dir():
                        self.inner_plugins_agent_dirs.append(agents_dir)
        
        self.local_agents_dir = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR")
    
    async def _load_agents(self) -> List[AgentInfo]:
        """Load agents from local directory."""
        all_agents_info = []
        
        # First, load agents from inner_plugins agents directories (if exist)
        for agents_dir in self.inner_plugins_agent_dirs:
            try:
                self.cli.console.print(f"📦 Loading built-in agents from: {agents_dir}")
                init_agents(str(agents_dir))
                inner_agents = LocalAgentRegistry.list_agents()
                for agent in inner_agents:
                    agent_info = AgentInfo.from_source(agent, source_location=str(agents_dir))
                    all_agents_info.append(agent_info)
            except Exception as e:
                self.cli.console.print(f"[dim]⚠️ Failed to load built-in agents from {agents_dir}: {e}[/dim]")
        
        # Then, load agents from configured directory
        if self.local_agents_dir:
            self.cli.console.print(f"📂 Loading local agents from: {self.local_agents_dir}")
            init_agents(self.local_agents_dir)
        else:
            # Use current working directory as default
            default_dir = os.getcwd()
            self.cli.console.print(f"📂 LOCAL_AGENTS_DIR not set. Using current directory: {default_dir}")
            init_agents(default_dir)
            self.local_agents_dir = default_dir
        
        # Get all agents from registry (including both inner_plugins and local_agents_dir)
        agents = LocalAgentRegistry.list_agents()
        if not agents:
            self.cli.console.print("[red]❌ No agents registered.[/red]")
            self.cli.console.print("Please configure LOCAL_AGENTS_DIR in .env or ensure agents are defined correctly.")
            return []
        
        # For agents not already in all_agents_info, add them with configured location
        existing_names = {agent_info.name for agent_info in all_agents_info}
        source_location = self.local_agents_dir or os.getcwd()
        for agent in agents:
            if agent.name not in existing_names:
                agent_info = AgentInfo.from_source(agent, source_location=source_location)
                all_agents_info.append(agent_info)
        
        return all_agents_info
    
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

