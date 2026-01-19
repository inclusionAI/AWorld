"""
Remote runtime for CLI protocols.
Interacts with agents via remote HTTP API.
"""
import httpx
from typing import List, Optional
from .base import BaseAgentRuntime
from ..models import AgentInfo
from ..executors import AgentExecutor
from ..executors.remote import RemoteAgentExecutor


class RemoteRuntime(BaseAgentRuntime):
    """
    Remote runtime for AWorldApp using aworld-cli.
    Interacts with agents via remote HTTP API.
    
    Example:
        >>> runtime = RemoteRuntime("http://localhost:8000")
        >>> await runtime.start()
    """
    
    def __init__(self, backend_url: str, agent_name: Optional[str] = None):
        """
        Initialize Remote Runtime.
        
        Args:
            backend_url: Backend server URL
            agent_name: The name of the agent to interact with. If None, user will be prompted to select.
        """
        super().__init__(agent_name)
        self.backend_url = backend_url.rstrip("/")
    
    async def _load_agents(self) -> List[AgentInfo]:
        """Load agents from remote backend."""
        self.cli.console.print(f"🌐 Connecting to remote backend: {self.backend_url}")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.backend_url}/agents")
                response.raise_for_status()
                agents_data = response.json()
            
            if not agents_data:
                self.cli.console.print("[red]❌ No agents found on remote server.[/red]")
                return []
            
            return [AgentInfo.from_dict(data, source_location=self.remote_backend) for data in agents_data]
            
        except httpx.RequestError as e:
            self.cli.console.print(f"[red]❌ Could not connect to remote server: {e}[/red]")
            return []
        except Exception as e:
            self.cli.console.print(f"[red]❌ Error loading agents: {e}[/red]")
            return []
    
    async def _create_executor(self, agent: AgentInfo) -> Optional[AgentExecutor]:
        """Create executor for remote agent."""
        return RemoteAgentExecutor(self.backend_url, agent.name, console=self.cli.console)
    
    def _get_source_type(self) -> str:
        """Get source type for display."""
        return "REMOTE"
    
    def _get_source_location(self) -> str:
        """Get source location for display."""
        return self.backend_url

__all__ = ["RemoteRuntime"]

