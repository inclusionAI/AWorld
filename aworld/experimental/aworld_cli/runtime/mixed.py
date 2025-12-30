"""
Mixed runtime that supports both local and remote agents from multiple sources.
"""
import os
from datetime import datetime
from typing import List, Optional, Dict
from aworld.core.context.amni import ApplicationContext, TaskInput
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel
import httpx
from .base import BaseAgentRuntime
from ..models import AgentInfo
from ..executors import AgentExecutor
from ..executors.local import LocalAgentExecutor
from ..executors.remote import RemoteAgentExecutor
from ..core.agent_registry import LocalAgentRegistry
from ..core.loader import init_agents


class MixedRuntime(BaseAgentRuntime):
    """
    Mixed runtime that supports both local and remote agents.
    Supports multiple local directories and multiple remote backends.
    
    Configuration:
        LOCAL_AGENTS_DIR: Semicolon-separated list of local directories
        REMOTE_AGENT_BACKEND: Semicolon-separated list of remote backend URLs
        
    Example:
        >>> runtime = MixedRuntime()
        >>> await runtime.start()
    """
    
    def __init__(
        self, 
        agent_name: Optional[str] = None, 
        remote_backends: Optional[List[str]] = None,
        local_dirs: Optional[List[str]] = None
    ):
        """
        Initialize Mixed Runtime.
        
        Args:
            agent_name: The name of the agent to interact with. If None, user will be prompted to select.
            remote_backends: Optional list of remote backend URLs (overrides environment variables)
            local_dirs: Optional list of local agent directories (overrides environment variables)
        """
        super().__init__(agent_name)
        self._parse_config(remote_backends, local_dirs)
        self._agent_sources: Dict[str, Dict] = {}  # agent_name -> {type, location, source}
    
    def _parse_config(
        self, 
        remote_backends: Optional[List[str]] = None,
        local_dirs: Optional[List[str]] = None
    ):
        """
        Parse configuration from environment variables or provided parameters.
        
        Args:
            remote_backends: Optional list of remote backend URLs (overrides environment variables)
            local_dirs: Optional list of local agent directories (overrides environment variables)
        """
        # Get the inner_plugins directory path (relative to this file)
        import pathlib
        current_dir = pathlib.Path(__file__).parent.parent
        inner_plugins_dir = current_dir / "inner_plugins"
        
        # Scan inner_plugins for agents directories (e.g., inner_plugins/*/agents)
        inner_plugins_agent_dirs = []
        if inner_plugins_dir.exists() and inner_plugins_dir.is_dir():
            for plugin_dir in inner_plugins_dir.iterdir():
                if plugin_dir.is_dir():
                    agents_dir = plugin_dir / "agents"
                    if agents_dir.exists() and agents_dir.is_dir():
                        inner_plugins_agent_dirs.append(str(agents_dir))
        
        # Use provided local_dirs if available, otherwise use environment variables
        if local_dirs:
            self.local_dirs = inner_plugins_agent_dirs + [d.strip() for d in local_dirs]
        else:
            # Parse LOCAL_AGENTS_DIR (semicolon-separated)
            local_dirs_str = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
            config_dirs = [d.strip() for d in local_dirs_str.split(";") if d.strip()]
            self.local_dirs = inner_plugins_agent_dirs + config_dirs
        
        # Parse REMOTE_AGENT_BACKEND or REMOTE_AGENTS_BACKEND (semicolon-separated)
        # Use provided remote_backends if available, otherwise use environment variables
        if remote_backends:
            self.remote_backends = [b.strip().rstrip("/") for b in remote_backends]
        else:
            remote_backends_str = os.getenv("REMOTE_AGENT_BACKEND") or os.getenv("REMOTE_AGENTS_BACKEND") or ""
            self.remote_backends = [b.strip().rstrip("/") for b in remote_backends_str.split(";") if b.strip()]
        
        # If no config (besides inner_plugins), use current working directory as default
        if len(self.local_dirs) == len(inner_plugins_agent_dirs) and not self.remote_backends:  # Only inner_plugins agents or empty
            self.local_dirs.append(os.getcwd())
    
    async def _load_agents(self) -> List[AgentInfo]:
        """Load agents from all configured sources."""
        all_agents: List[AgentInfo] = []
        agent_sources_map: Dict[str, Dict] = {}  # Track sources separately to avoid overwriting
        
        # Load from local directories
        for local_dir in self.local_dirs:
            try:
                self.cli.console.print(f"📂 Loading local agents from: {local_dir}")
                
                # Try new @agent decorator first
                try:
                    init_agents(local_dir)
                    local_agents = LocalAgentRegistry.list_agents()
                    for agent in local_agents:
                        agent_info = AgentInfo.from_local_agent(agent, source_location=local_dir)
                        # Store source information for executor creation (only if not already set)
                        if agent_info.name not in agent_sources_map:
                            agent_sources_map[agent_info.name] = {
                                "type": "local",
                                "location": local_dir,
                                "source": agent
                            }
                        all_agents.append(agent_info)
                except Exception as e:
                    self.cli.console.print(f"[dim]⚠️ Failed to load with @agent decorator: {e}[/dim]")
                        
            except Exception as e:
                self.cli.console.print(f"[yellow]⚠️ Failed to load from {local_dir}: {e}[/yellow]")
        
        # Load from remote backends
        for backend_url in self.remote_backends:
            try:
                self.cli.console.print(f"🌐 Connecting to remote backend: {backend_url}")
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{backend_url}/agents", timeout=10.0)
                    response.raise_for_status()
                    agents_data = response.json()
                    
                    for data in agents_data:
                        agent_info = AgentInfo.from_dict(data, source_location=backend_url)
                        # Only store remote source if local source doesn't exist (prioritize LOCAL)
                        if agent_info.name not in agent_sources_map:
                            agent_sources_map[agent_info.name] = {
                                "type": "remote",
                                "location": backend_url,
                                "source": data
                            }
                        all_agents.append(agent_info)
            except Exception as e:
                self.cli.console.print(f"[yellow]⚠️ Failed to load from {backend_url}: {e}[/yellow]")
        
        # Remove duplicates (prioritize LOCAL over REMOTE)
        seen = {}
        unique_agents = []
        for agent in all_agents:
            if agent.name not in seen:
                seen[agent.name] = agent
                unique_agents.append(agent)
            else:
                # If duplicate found, prioritize LOCAL over REMOTE
                existing_agent = seen[agent.name]
                existing_is_local = existing_agent.source_type == "LOCAL"
                new_is_local = agent.source_type == "LOCAL"
                
                # If existing is LOCAL, keep it (ignore remote duplicate)
                if existing_is_local and not new_is_local:
                    # Keep existing LOCAL, ignore remote duplicate
                    self.cli.console.print(f"[dim]⚠️ Duplicate agent '{agent.name}' found (remote), keeping local version[/dim]")
                elif not existing_is_local and new_is_local:
                    # Replace REMOTE with LOCAL
                    seen[agent.name] = agent
                    # Replace in unique_agents list
                    for i, a in enumerate(unique_agents):
                        if a.name == agent.name:
                            unique_agents[i] = agent
                            break
                    self.cli.console.print(f"[dim]⚠️ Duplicate agent '{agent.name}' found, replacing remote with local version[/dim]")
                else:
                    # Both same type (both LOCAL or both REMOTE), keep first and log
                    self.cli.console.print(f"[dim]⚠️ Duplicate agent '{agent.name}' found, keeping first occurrence[/dim]")
        
        # Update _agent_sources based on final unique_agents (prioritize LOCAL)
        self._agent_sources.clear()
        for agent in unique_agents:
            if agent.name in agent_sources_map:
                self._agent_sources[agent.name] = agent_sources_map[agent.name]
        
        if not unique_agents:
            self.cli.console.print("[red]❌ No agents found from any source.[/red]")
        
        return unique_agents
    
    async def _create_executor(self, agent: AgentInfo) -> Optional[AgentExecutor]:
        """Create executor based on agent source."""
        source_info = self._agent_sources.get(agent.name)
        if not source_info:
            self.cli.console.print(f"[red]❌ Source information not found for agent '{agent.name}'[/red]")
            return None
        
        if source_info["type"] == "local":
            return await self._create_local_executor(agent, source_info)
        elif source_info["type"] == "remote":
            return self._create_remote_executor(agent, source_info)
        else:
            self.cli.console.print(f"[red]❌ Unknown source type for agent '{agent.name}'[/red]")
            return None
    
    async def _create_local_executor(self, agent: AgentInfo, source_info: Dict) -> Optional[AgentExecutor]:
        """Create executor for local agent."""
        try:
            source = source_info["source"]
            
            # Get context config from source if available
            # env_config from env TODO
            context_config = AmniConfigFactory.create(
                AmniConfigLevel.NAVIGATOR,
                debug_mode=True
            )
            context_config.agent_config.history_scope = "session"

            # Try to get swarm without context first (for swarm instances or functions that don't need context)
            try:
                swarm = await source.get_swarm(None)
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
                swarm = await source.get_swarm(temp_context)
            
            return LocalAgentExecutor(swarm, context_config=context_config, console=self.cli.console)
            
        except Exception as e:
            self.cli.console.print(f"[red]❌ Failed to initialize local agent session: {e}[/red]")
            import traceback
            traceback.print_exc()
            return None
    
    def _create_remote_executor(self, agent: AgentInfo, source_info: Dict) -> AgentExecutor:
        """Create executor for remote agent."""
        backend_url = source_info["location"]
        return RemoteAgentExecutor(backend_url, agent.name, console=self.cli.console)
    
    def _get_source_type(self) -> str:
        """Get source type for display."""
        types = []
        if self.local_dirs:
            types.append("LOCAL")
        if self.remote_backends:
            types.append("REMOTE")
        return "+".join(types) if types else "MIXED"
    
    def _get_source_location(self) -> str:
        """Get source location for display."""
        locations = []
        if self.local_dirs:
            locations.extend(self.local_dirs)
        if self.remote_backends:
            locations.extend(self.remote_backends)
        return "; ".join(locations) if locations else ""

__all__ = ["MixedRuntime"]

