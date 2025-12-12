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
from ..core.registry import LocalAgentRegistry
from ..core.loader import init_agents

# Try to import aworldappinfra for backward compatibility
try:
    from aworldappinfra.core.registry import AgentTeamRegistry
    from aworldappinfra.utils.team_loader import init_teams
    HAS_AWORLDAPPINFRA = True
except ImportError:
    HAS_AWORLDAPPINFRA = False
    AgentTeamRegistry = None
    init_teams = None


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
    
    def __init__(self, agent_name: Optional[str] = None):
        """
        Initialize Mixed Runtime.
        
        Args:
            agent_name: The name of the agent to interact with. If None, user will be prompted to select.
        """
        super().__init__(agent_name)
        self._parse_config()
        self._agent_sources: Dict[str, Dict] = {}  # agent_name -> {type, location, source}
    
    def _parse_config(self):
        """Parse configuration from environment variables."""
        # Parse LOCAL_AGENTS_DIR (semicolon-separated)
        local_dirs_str = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
        self.local_dirs = [d.strip() for d in local_dirs_str.split(";") if d.strip()]
        
        # Parse REMOTE_AGENT_BACKEND (semicolon-separated)
        remote_backends_str = os.getenv("REMOTE_AGENT_BACKEND") or ""
        self.remote_backends = [b.strip().rstrip("/") for b in remote_backends_str.split(";") if b.strip()]
        
        # If no config, use current working directory as default
        if not self.local_dirs and not self.remote_backends:
            self.local_dirs = [os.getcwd()]
    
    async def _load_agents(self) -> List[AgentInfo]:
        """Load agents from all configured sources."""
        all_agents: List[AgentInfo] = []
        
        # Load from local directories
        for local_dir in self.local_dirs:
            try:
                self.cli.console.print(f"📂 Loading local agents from: {local_dir}")
                
                # Try new @agent decorator first
                try:
                    init_agents(local_dir)
                    local_agents = LocalAgentRegistry.list_agents()
                    for agent in local_agents:
                        agent_info = AgentInfo.from_source(agent)
                        # Store source information for executor creation
                        self._agent_sources[agent_info.name] = {
                            "type": "local",
                            "location": local_dir,
                            "source": agent
                        }
                        all_agents.append(agent_info)
                except Exception as e:
                    self.cli.console.print(f"[dim]⚠️ Failed to load with @agent decorator: {e}[/dim]")
                
                # Try old @agent_team decorator for backward compatibility
                if HAS_AWORLDAPPINFRA:
                    try:
                        init_teams(local_dir)
                        teams = AgentTeamRegistry.list_team()
                        for team in teams:
                            # Skip if already loaded from @agent
                            if team.name not in [a.name for a in all_agents]:
                                agent_info = AgentInfo.from_team(team)
                                # Store source information for executor creation
                                self._agent_sources[agent_info.name] = {
                                    "type": "local",
                                    "location": local_dir,
                                    "source": team
                                }
                                all_agents.append(agent_info)
                    except Exception as e:
                        self.cli.console.print(f"[dim]⚠️ Failed to load with @agent_team decorator: {e}[/dim]")
                        
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
                        agent_info = AgentInfo.from_dict(data)
                        # Store source information for executor creation
                        self._agent_sources[agent_info.name] = {
                            "type": "remote",
                            "location": backend_url,
                            "source": data
                        }
                        all_agents.append(agent_info)
            except Exception as e:
                self.cli.console.print(f"[yellow]⚠️ Failed to load from {backend_url}: {e}[/yellow]")
        
        # Remove duplicates (keep first occurrence)
        seen = set()
        unique_agents = []
        for agent in all_agents:
            if agent.name not in seen:
                seen.add(agent.name)
                unique_agents.append(agent)
            else:
                self.cli.console.print(f"[yellow]⚠️ Duplicate agent '{agent.name}' found, keeping first occurrence[/yellow]")
        
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
            
            # Check if it's a LocalAgent (new) or AgentTeam (old, backward compatibility)
            from ..core.registry import LocalAgent
            is_local_agent = isinstance(source, LocalAgent)
            
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

