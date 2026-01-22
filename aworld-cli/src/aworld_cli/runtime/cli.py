"""
CLI runtime that supports agents from multiple sources (plugins, local, remote).

Uses composition pattern with abstract loaders to follow unified lifecycle:
1. Load plugins (skills + agents) - Load phase
2. Load local agents - Load phase
3. Load remote agents - Load phase
4. Create executors - Run phase (handled here)

Load and Run are separated:
- Loaders: Only responsible for loading agents (load phase)
- Runtime: Responsible for creating executors and running (run phase)
"""
import os
from datetime import datetime
from typing import List, Optional, Dict
from pathlib import Path
from .base import BaseCliRuntime
from .loaders import AgentLoader, PluginLoader, LocalAgentLoader, RemoteAgentLoader
from ..models import AgentInfo
from ..executors import AgentExecutor
from ..executors.local import LocalAgentExecutor
from ..executors.remote import RemoteAgentExecutor
from ..core.agent_registry import LocalAgentRegistry
from aworld.core.context.amni import ApplicationContext, TaskInput
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel


class CliRuntime(BaseCliRuntime):
    """
    CLI runtime that supports agents from multiple sources.
    
    Supports plugins, local directories, and remote backends.
    No distinction between local and remote - unified lifecycle.
    
    Uses composition pattern with abstract loaders:
    - PluginLoader: Loads plugins (skills + agents) - Load phase
    - LocalAgentLoader: Loads local agents - Load phase
    - RemoteAgentLoader: Loads remote agents - Load phase
    - CliRuntime: Creates executors - Run phase
    
    Unified lifecycle:
    1. Load plugins (for each plugin: load skills, then load agents)
    2. Load local agents
    3. Load remote agents
    4. Create executors (when needed, based on agent source type)
    
    Configuration:
        LOCAL_AGENTS_DIR: Semicolon-separated list of local directories
        REMOTE_AGENT_BACKEND: Semicolon-separated list of remote backend URLs
        
    Example:
        >>> runtime = CliRuntime()
        >>> await runtime.start()
    """
    
    def __init__(
        self, 
        agent_name: Optional[str] = None, 
        remote_backends: Optional[List[str]] = None,
        local_dirs: Optional[List[str]] = None,
        session_id: Optional[str] = None
    ):
        """
        Initialize CLI Runtime.
        
        Args:
            agent_name: The name of the agent to interact with. If None, user will be prompted to select.
            remote_backends: Optional list of remote backend URLs (overrides environment variables)
            local_dirs: Optional list of local agent directories (overrides environment variables)
            session_id: Optional session ID to use when creating executors
        """
        super().__init__(agent_name)
        self._parse_config(remote_backends, local_dirs)
        
        # Track agent sources for executor creation: agent_name -> {type, location, ...}
        self._agent_sources: Dict[str, Dict] = {}
        # Store session_id for executor creation
        self._session_id = session_id
    
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
        # Get plugin directories
        self.plugin_dirs = self._get_plugin_dirs()
        
        # Parse local directories
        if local_dirs:
            self.local_dirs = [d.strip() for d in local_dirs]
        else:
            local_dirs_str = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
            self.local_dirs = [d.strip() for d in local_dirs_str.split(";") if d.strip()]
        
        # Parse remote backends
        if remote_backends:
            self.remote_backends = [b.strip().rstrip("/") for b in remote_backends]
        else:
            remote_backends_str = os.getenv("REMOTE_AGENT_BACKEND") or os.getenv("REMOTE_AGENTS_BACKEND") or ""
            self.remote_backends = [b.strip().rstrip("/") for b in remote_backends_str.split(";") if b.strip()]
        
        # If no config, use current working directory as default
        if not self.plugin_dirs and not self.local_dirs and not self.remote_backends:
            self.local_dirs.append(os.getcwd())
    
    def _get_plugin_dirs(self) -> List[Path]:
        """
        Get all plugin directories (built-in and installed).
        
        Returns:
            List of plugin directory paths
        """
        plugin_dirs = []
        
        # Get built-in plugins (inner_plugins)
        import pathlib
        current_dir = pathlib.Path(__file__).parent.parent
        inner_plugins_dir = current_dir / "inner_plugins"
        
        if inner_plugins_dir.exists() and inner_plugins_dir.is_dir():
            for plugin_dir in inner_plugins_dir.iterdir():
                if plugin_dir.is_dir():
                    plugin_dirs.append(plugin_dir)
        
        # Get installed plugins
        try:
            from ..core.plugin_manager import PluginManager
            plugin_manager = PluginManager()
            installed_plugin_dirs = plugin_manager.get_plugin_dirs()
            # Convert agent dirs back to plugin dirs (parent directory)
            for agent_dir in installed_plugin_dirs:
                plugin_dir = agent_dir.parent
                if plugin_dir not in plugin_dirs:
                    plugin_dirs.append(plugin_dir)
            
            if installed_plugin_dirs and hasattr(self, 'cli') and hasattr(self.cli, 'console'):
                self.cli.console.print(f"📦 Found {len(installed_plugin_dirs)} installed plugin(s)")
        except Exception as e:
            # Fail silently if plugin manager is not available
            pass
        
        return plugin_dirs
    
    async def _load_skills(self) -> Dict[str, int]:
        """
        Load skills from all plugin directories.
        
        Searches for skills in plugin_dir/skills directory for each plugin.
        Only directories containing SKILL.md file are considered as skills.
        Skills are registered into the global skill registry.
        
        Returns:
            Dictionary mapping plugin names to number of skills loaded
        """
        from ..core.skill_registry import get_skill_registry
        
        registry = get_skill_registry()
        loaded_skills: Dict[str, int] = {}
        
        for plugin_dir in self.plugin_dirs:
            skills_dir = plugin_dir / "skills"
            
            if not skills_dir.exists() or not skills_dir.is_dir():
                continue
            
            try:
                # Check for subdirectories containing SKILL.md files
                skill_count = 0
                for subdir in skills_dir.iterdir():
                    if not subdir.is_dir():
                        continue
                    
                    # Only consider directories that contain SKILL.md file
                    skill_md_file = subdir / "SKILL.md"
                    if skill_md_file.exists() and skill_md_file.is_file():
                        skill_count += 1
                
                # Only register if there are valid skill directories (with SKILL.md)
                if skill_count > 0:
                    count = registry.register_source(str(skills_dir), source_name=str(skills_dir))
                    plugin_name = plugin_dir.name
                    loaded_skills[plugin_name] = count
                    
                    if hasattr(self, 'cli') and hasattr(self.cli, 'console') and self.cli.console:
                        if count > 0:
                            self.cli.console.print(f"[dim]📚 Loaded {count} skill(s) from plugin: {plugin_name}[/dim]")
                else:
                    # No valid skill directories found (no SKILL.md files)
                    plugin_name = plugin_dir.name
                    loaded_skills[plugin_name] = 0
            except Exception as e:
                plugin_name = plugin_dir.name
                if hasattr(self, 'cli') and hasattr(self.cli, 'console') and self.cli.console:
                    self.cli.console.print(f"[yellow]⚠️ Failed to load skills from plugin {plugin_name}: {e}[/yellow]")
                loaded_skills[plugin_name] = 0
        
        return loaded_skills
    
    async def _load_agents(self) -> List[AgentInfo]:
        """
        Load agents following unified lifecycle (Load phase):
        1. Load plugins (skills + agents)
        2. Load local agents
        3. Load remote agents
        
        Uses abstract loaders to eliminate code duplication.
        Loaders are responsible ONLY for loading, not for creating executors.
        
        Returns:
            List of all loaded AgentInfo objects (deduplicated, prioritizing local over remote)
        """
        all_agents: List[AgentInfo] = []
        agent_sources_map: Dict[str, Dict] = {}  # Track sources for executor creation
        
        # ========== Lifecycle Step 1: Load Plugins ==========
        # For each plugin: load skills, then load agents
        for plugin_dir in self.plugin_dirs:
            try:
                loader = PluginLoader(plugin_dir, console=self.cli.console)
                
                # Load agents from plugin (this also loads skills internally)
                plugin_agents = await loader.load_agents()
                
                # Track source information
                for agent in plugin_agents:
                    if agent.name not in agent_sources_map:
                        agent_sources_map[agent.name] = {
                            "type": "plugin",
                            "location": str(plugin_dir),
                            "agents_dir": str(plugin_dir / "agents")  # Store agents dir for executor creation
                        }
                        all_agents.append(agent)
                    else:
                        self.cli.console.print(f"[dim]⚠️ Duplicate agent '{agent.name}' from plugin, keeping first[/dim]")
                        
            except Exception as e:
                self.cli.console.print(f"[yellow]⚠️ Failed to load plugin {plugin_dir}: {e}[/yellow]")
        
        # ========== Lifecycle Step 2: Load Local Agents ==========
        if self.local_dirs:
            self.cli.console.print(f"[dim]📂 Loading local agents from {len(self.local_dirs)} directory(ies)...[/dim]")
        
        local_agents_count = 0
        for local_dir in self.local_dirs:
            try:
                self.cli.console.print(f"[dim]  📁 Scanning local directory: {local_dir}[/dim]")
                loader = LocalAgentLoader(local_dir, console=self.cli.console)
                
                # Load agents from local directory
                local_agents = await loader.load_agents()
                
                if local_agents:
                    self.cli.console.print(f"[dim]  ✅ Found {len(local_agents)} agent(s) in {local_dir}[/dim]")
                    local_agents_count += len(local_agents)
                else:
                    self.cli.console.print(f"[dim]  ℹ️  No agents found in {local_dir}[/dim]")
                
                # Track source information (prioritize local over remote)
                for agent in local_agents:
                    if agent.name not in agent_sources_map:
                        agent_sources_map[agent.name] = {
                            "type": "local",
                            "location": local_dir
                        }
                        all_agents.append(agent)
                        self.cli.console.print(f"[dim]    ✓ Loaded agent: {agent.name} (local)[/dim]")
                    else:
                        existing_source = agent_sources_map[agent.name]
                        if existing_source["type"] == "local":
                            self.cli.console.print(f"[dim]    ⚠️ Duplicate agent '{agent.name}' found, keeping first occurrence[/dim]")
                        else:
                            # Replace remote/plugin with local (prioritize LOCAL)
                            agent_sources_map[agent.name] = {
                                "type": "local",
                                "location": local_dir
                            }
                            # Replace in all_agents list
                            for i, a in enumerate(all_agents):
                                if a.name == agent.name:
                                    all_agents[i] = agent
                                    break
                            self.cli.console.print(f"[dim]    ⚠️ Duplicate agent '{agent.name}' found, replacing {existing_source['type']} version with local[/dim]")
                        
            except Exception as e:
                self.cli.console.print(f"[yellow]⚠️ Failed to load from {local_dir}: {e}[/yellow]")
        
        if self.local_dirs and local_agents_count > 0:
            self.cli.console.print(f"[dim]📊 Total local agents loaded: {local_agents_count}[/dim]")
        
        # ========== Lifecycle Step 3: Load Remote Agents ==========
        if self.remote_backends:
            self.cli.console.print(f"[dim]🌐 Loading remote agents from {len(self.remote_backends)} backend(s)...[/dim]")
        
        remote_agents_count = 0
        for backend_url in self.remote_backends:
            try:
                self.cli.console.print(f"[dim]  🔗 Connecting to remote backend: {backend_url}[/dim]")
                loader = RemoteAgentLoader(backend_url, console=self.cli.console)
                
                # Load agents from remote backend
                remote_agents = await loader.load_agents()
                
                if remote_agents:
                    self.cli.console.print(f"[dim]  ✅ Found {len(remote_agents)} agent(s) from {backend_url}[/dim]")
                    remote_agents_count += len(remote_agents)
                else:
                    self.cli.console.print(f"[dim]  ℹ️  No agents found from {backend_url}[/dim]")
                
                # Track source information (only if local doesn't exist)
                for agent in remote_agents:
                    if agent.name not in agent_sources_map:
                        agent_sources_map[agent.name] = {
                            "type": "remote",
                            "location": backend_url
                        }
                        all_agents.append(agent)
                        self.cli.console.print(f"[dim]    ✓ Loaded agent: {agent.name} (remote)[/dim]")
                    else:
                        # Local/plugin source exists, skip remote duplicate
                        existing_source = agent_sources_map[agent.name]
                        self.cli.console.print(f"[dim]    ⚠️ Duplicate agent '{agent.name}' found (remote), keeping {existing_source['type']} version[/dim]")
                        
            except Exception as e:
                self.cli.console.print(f"[yellow]⚠️ Failed to load from {backend_url}: {e}[/yellow]")
        
        if self.remote_backends and remote_agents_count > 0:
            self.cli.console.print(f"[dim]📊 Total remote agents loaded: {remote_agents_count}[/dim]")
        
        # Update _agent_sources based on final agents
        self._agent_sources.clear()
        for agent in all_agents:
            if agent.name in agent_sources_map:
                self._agent_sources[agent.name] = agent_sources_map[agent.name]
        
        # Summary log
        plugin_count = len([a for a in all_agents if agent_sources_map.get(a.name, {}).get("type") == "plugin"])
        local_count = len([a for a in all_agents if agent_sources_map.get(a.name, {}).get("type") == "local"])
        remote_count = len([a for a in all_agents if agent_sources_map.get(a.name, {}).get("type") == "remote"])
        
        if all_agents:
            self.cli.console.print(f"[green]✅ Agent loading complete: {len(all_agents)} total agent(s) (plugin: {plugin_count}, local: {local_count}, remote: {remote_count})[/green]")
        
        if not all_agents:
            self.cli.console.print("[red]❌ No agents found from any source.[/red]")
        
        return all_agents
    
    async def _create_executor(self, agent: AgentInfo) -> Optional[AgentExecutor]:
        """
        Create executor based on agent source type (Run phase).
        
        Load and Run are separated:
        - Load phase: Loaders load agents
        - Run phase: Runtime creates executors based on source type
        
        This method handles executor creation for all source types:
        - plugin/local: Creates LocalAgentExecutor
        - remote: Creates RemoteAgentExecutor
        
        Args:
            agent: AgentInfo object
            
        Returns:
            AgentExecutor instance or None if creation failed
        """
        source_info = self._agent_sources.get(agent.name)
        if not source_info:
            self.cli.console.print(f"[red]❌ Source information not found for agent '{agent.name}'[/red]")
            return None
        
        source_type = source_info.get("type")
        
        # Create executor based on source type
        if source_type in ["plugin", "local"]:
            return await self._create_local_executor(agent, source_info)
        elif source_type == "remote":
            return self._create_remote_executor(agent, source_info)
        else:
            self.cli.console.print(f"[red]❌ Unknown source type '{source_type}' for agent '{agent.name}'[/red]")
            return None
    
    async def _create_local_executor(
        self, 
        agent: AgentInfo, 
        source_info: Dict
    ) -> Optional[AgentExecutor]:
        """
        Create executor for local/plugin agent.
        
        Args:
            agent: AgentInfo object
            source_info: Source information dictionary
            
        Returns:
            LocalAgentExecutor instance or None if creation failed
        """
        try:
            # Get the agent from registry
            local_agent = LocalAgentRegistry.get_agent(agent.name)
            if not local_agent:
                self.cli.console.print(f"[red]❌ Agent '{agent.name}' not found in registry.[/red]")
                return None
            
            # Get context config from agent if available
            context_config = (
                local_agent.context_config 
                if hasattr(local_agent, 'context_config') 
                else AmniConfigFactory.create(AmniConfigLevel.NAVIGATOR, debug_mode=True)
            )
            context_config.agent_config.history_scope = "session"
            
            # Get hooks from agent if available (support both LocalAgent and AgentTeam)
            hooks = None
            if hasattr(local_agent, 'hooks') and local_agent.hooks:
                hooks = local_agent.hooks
            # Also check if source is AgentTeam (from aworld-app-infra)
            elif hasattr(agent, 'source') and agent.source:
                source = agent.source
                if hasattr(source, 'hooks') and source.hooks:
                    hooks = source.hooks
            
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
            
            return LocalAgentExecutor(
                swarm, 
                context_config=context_config, 
                console=self.cli.console,
                session_id=self._session_id,
                hooks=hooks
            )
            
        except Exception as e:
            self.cli.console.print(f"[red]❌ Failed to initialize local agent session: {e}[/red]")
            import traceback
            traceback.print_exc()
            return None
    
    def _create_remote_executor(
        self, 
        agent: AgentInfo, 
        source_info: Dict
    ) -> AgentExecutor:
        """
        Create executor for remote agent.
        
        Args:
            agent: AgentInfo object
            source_info: Source information dictionary
            
        Returns:
            RemoteAgentExecutor instance
        """
        backend_url = source_info["location"]
        return RemoteAgentExecutor(
            backend_url, 
            agent.name, 
            console=self.cli.console,
            session_id=self._session_id
        )
    
    def _get_source_type(self) -> str:
        """Get source type for display."""
        types = []
        if self.plugin_dirs:
            types.append("PLUGIN")
        if self.local_dirs:
            types.append("LOCAL")
        if self.remote_backends:
            types.append("REMOTE")
        return "+".join(types) if types else "CLI"
    
    def _get_source_location(self) -> str:
        """Get source location for display."""
        locations = []
        if self.plugin_dirs:
            locations.extend([str(d) for d in self.plugin_dirs])
        if self.local_dirs:
            locations.extend(self.local_dirs)
        if self.remote_backends:
            locations.extend(self.remote_backends)
        return "; ".join(locations) if locations else ""

__all__ = ["CliRuntime"]
