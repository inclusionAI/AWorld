"""
Agent loaders for different sources.

Provides abstract base class and concrete implementations for loading agents
from different sources (plugins, local directories, remote backends).

Loaders are responsible ONLY for loading agents (load phase).
Executor creation is handled by Runtime (run phase).

Lifecycle:
1. Load plugins (skills + agents)
2. Load local agents
3. Load remote agents
"""
from abc import ABC, abstractmethod
from typing import List, Dict
from pathlib import Path
from ..models import AgentInfo


class AgentLoader(ABC):
    """
    Abstract base class for loading agents from different sources.
    
    Loaders are responsible ONLY for loading agents (load phase).
    They do NOT create executors - that's the responsibility of Runtime (run phase).
    
    Example:
        >>> loader = LocalAgentLoader("/path/to/agents")
        >>> agents = await loader.load_agents()
    """
    
    def __init__(self, console=None):
        """
        Initialize agent loader.
        
        Args:
            console: Optional Rich console for output
        """
        self.console = console
        self._loaded_agents: Dict[str, AgentInfo] = {}  # agent_name -> AgentInfo
    
    @abstractmethod
    async def load_agents(self) -> List[AgentInfo]:
        """
        Load agents from source.
        
        Returns:
            List of AgentInfo objects
        """
        pass
    
    def get_loaded_agents(self) -> Dict[str, AgentInfo]:
        """
        Get all loaded agents as a dictionary.
        
        Returns:
            Dictionary mapping agent names to AgentInfo objects
        """
        return self._loaded_agents.copy()


class PluginLoader(AgentLoader):
    """
    Loader for plugins (both built-in and installed).
    
    Handles loading skills and agents from plugins.
    Lifecycle:
    1. Load skills from plugin
    2. Load agents from plugin
    
    Example:
        >>> loader = PluginLoader(Path("/path/to/plugin"))
        >>> agents = await loader.load_agents()
    """
    
    def __init__(self, plugin_path: Path, console=None):
        """
        Initialize plugin loader.
        
        Args:
            plugin_path: Path to plugin directory
            console: Optional Rich console for output
        """
        super().__init__(console)
        self.plugin_path = Path(plugin_path)
        self.skills_dir = self.plugin_path / "skills"
        self.agents_dir = self.plugin_path / "agents"
    
    async def _load_skills(self) -> None:
        """
        Load skills from plugin.
        
        Skills are loaded into the global skill registry.
        """
        if not self.skills_dir.exists():
            return
        
        try:
            from ..core.skill_registry import get_skill_registry
            registry = get_skill_registry()
            # Register skills from this plugin
            registry.register_source(str(self.skills_dir))
            if self.console:
                self.console.print(f"[dim]📚 Loaded skills from plugin: {self.plugin_path.name}[/dim]")
        except Exception as e:
            if self.console:
                self.console.print(f"[yellow]⚠️ Failed to load skills from {self.plugin_path}: {e}[/yellow]")
    
    async def _load_agents_from_plugin(self) -> List[AgentInfo]:
        """
        Load agents from plugin directory.
        
        Returns:
            List of AgentInfo objects (filtered to only include agents from inner_plugins directory)
        """
        if not self.agents_dir.exists():
            return []
        
        try:
            from ..core.loader import init_agents
            from ..core.agent_registry import LocalAgentRegistry
            from pathlib import Path
            
            # Load agents from plugin directory
            init_agents(str(self.agents_dir))
            
            # Get agents from registry
            agents = LocalAgentRegistry.list_agents()
            agents_info = []
            
            # Filter agents: only keep those from inner_plugins directory
            for agent in agents:
                # Only include agents that have register_dir set and contain "inner_plugins"
                if agent.register_dir:
                    register_dir_path = Path(agent.register_dir)
                    # Check if "inner_plugins" is in the path
                    if "inner_plugins" in str(register_dir_path):
                        agent_info = AgentInfo.from_local_agent(agent, source_location=str(self.agents_dir))
                        agents_info.append(agent_info)
                        self._loaded_agents[agent_info.name] = agent_info
                # If register_dir is not set, skip the agent (cannot determine if from inner_plugins)
            
            return agents_info
        except Exception as e:
            if self.console:
                self.console.print(f"[yellow]⚠️ Failed to load agents from {self.agents_dir}: {e}[/yellow]")
            return []
    
    async def load_agents(self) -> List[AgentInfo]:
        """
        Load agents from plugin following the lifecycle:
        1. Load skills
        2. Load agents
        
        Returns:
            List of AgentInfo objects
        """
        # Step 1: Load skills
        await self._load_skills()
        
        # Step 2: Load agents
        return await self._load_agents_from_plugin()


class LocalAgentLoader(AgentLoader):
    """
    Loader for local agent directories.
    
    Only responsible for loading agents (load phase).
    Executor creation is handled by Runtime (run phase).
    
    Example:
        >>> loader = LocalAgentLoader("/path/to/agents", console=console)
        >>> agents = await loader.load_agents()
    """
    
    def __init__(self, agent_dir: str, console=None):
        """
        Initialize local agent loader.
        
        Args:
            agent_dir: Directory path containing agents
            console: Optional Rich console for output
        """
        super().__init__(console)
        self.agent_dir = agent_dir
    
    async def load_agents(self) -> List[AgentInfo]:
        """
        Load agents from local directory.
        
        Returns:
            List of AgentInfo objects
        """
        try:
            from ..core.loader import init_agents
            from ..core.agent_registry import LocalAgentRegistry
            
            if self.console:
                self.console.print(f"[dim]📂 Loading local agents from: {self.agent_dir}[/dim]")
            
            # Load agents from directory
            init_agents(self.agent_dir)
            
            # Get agents from registry
            agents = LocalAgentRegistry.list_agents()
            agents_info = []
            
            for agent in agents:
                agent_info = AgentInfo.from_local_agent(agent, source_location=self.agent_dir)
                agents_info.append(agent_info)
                self._loaded_agents[agent_info.name] = agent_info
            
            return agents_info
        except Exception as e:
            if self.console:
                self.console.print(f"[yellow]⚠️ Failed to load from {self.agent_dir}: {e}[/yellow]")
            return []


class RemoteAgentLoader(AgentLoader):
    """
    Loader for remote agent backends.
    
    Only responsible for loading agents (load phase).
    Executor creation is handled by Runtime (run phase).
    
    Example:
        >>> loader = RemoteAgentLoader("http://localhost:8000", console=console)
        >>> agents = await loader.load_agents()
    """
    
    def __init__(self, backend_url: str, console=None):
        """
        Initialize remote agent loader.
        
        Args:
            backend_url: Backend server URL
            console: Optional Rich console for output
        """
        super().__init__(console)
        self.backend_url = backend_url.rstrip("/")
    
    async def load_agents(self) -> List[AgentInfo]:
        """
        Load agents from remote backend.
        
        Returns:
            List of AgentInfo objects
        """
        import httpx
        
        try:
            if self.console:
                self.console.print(f"🌐 Connecting to remote backend: {self.backend_url}")
            
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.backend_url}/agents", timeout=10.0)
                response.raise_for_status()
                agents_data = response.json()
            
            agents_info = []
            for data in agents_data:
                agent_info = AgentInfo.from_dict(data, source_location=self.backend_url)
                agents_info.append(agent_info)
                self._loaded_agents[agent_info.name] = agent_info
            
            return agents_info
        except Exception as e:
            if self.console:
                self.console.print(f"[yellow]⚠️ Failed to load from {self.backend_url}: {e}[/yellow]")
            return []


__all__ = ["AgentLoader", "PluginLoader", "LocalAgentLoader", "RemoteAgentLoader"]
