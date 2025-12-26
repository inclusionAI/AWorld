"""Main builder for creating Sandbox instances with fluent API."""
from typing import Dict, List, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aworld.sandbox.base import Sandbox

# Import here to avoid circular import
from aworld.sandbox.builder.agents_builder import AgentsBuilder


class SandboxBuilder:
    """Builder for creating Sandbox instances with fluent API."""
    
    def __init__(self):
        self._sandbox_id: Optional[str] = None
        self._env_type: Optional[int] = None
        self._metadata: Optional[Dict[str, str]] = None
        self._timeout: Optional[int] = None
        self._mcp_servers: Optional[List[str]] = None
        self._mcp_config: Optional[Any] = None
        self._black_tool_actions: Optional[Dict[str, List[str]]] = None
        self._skill_configs: Optional[Any] = None
        self._tools: Optional[List[str]] = None
        self._registry_url: Optional[str] = None
        self._custom_env_tools: Optional[Any] = None
        self._agents: Optional[Dict[str, Any]] = None
        self._agents_builder = AgentsBuilder(self)
    
    def sandbox_id(self, sandbox_id: str) -> 'SandboxBuilder':
        """Set sandbox ID."""
        # Auto-commit current agent if exists
        if self._agents_builder.current_agent is not None:
            self._agents_builder.current_agent._auto_commit()
        self._sandbox_id = sandbox_id
        return self
    
    def _auto_commit_current_agent(self):
        """Auto-commit current agent if exists."""
        if self._agents_builder.current_agent is not None:
            self._agents_builder.current_agent._auto_commit()
    
    def env_type(self, env_type: int) -> 'SandboxBuilder':
        """Set environment type."""
        self._auto_commit_current_agent()
        self._env_type = env_type
        return self
    
    def metadata(self, metadata: Dict[str, str]) -> 'SandboxBuilder':
        """Set sandbox metadata."""
        self._auto_commit_current_agent()
        self._metadata = metadata
        return self
    
    def timeout(self, timeout: int) -> 'SandboxBuilder':
        """Set timeout."""
        self._auto_commit_current_agent()
        self._timeout = timeout
        return self
    
    def mcp_servers(self, mcp_servers: List[str]) -> 'SandboxBuilder':
        """Set MCP servers list."""
        self._auto_commit_current_agent()
        self._mcp_servers = mcp_servers
        return self
    
    def mcp_config(self, mcp_config: Dict[str, Any]) -> 'SandboxBuilder':
        """Set MCP configuration."""
        self._auto_commit_current_agent()
        self._mcp_config = mcp_config
        return self
    
    def black_tool_actions(self, black_tool_actions: Dict[str, List[str]]) -> 'SandboxBuilder':
        """Set black tool actions."""
        self._auto_commit_current_agent()
        self._black_tool_actions = black_tool_actions
        return self
    
    def skill_configs(self, skill_configs: Any) -> 'SandboxBuilder':
        """Set skill configurations."""
        self._auto_commit_current_agent()
        self._skill_configs = skill_configs
        return self
    
    def tools(self, tools: List[str]) -> 'SandboxBuilder':
        """Set tools list."""
        self._auto_commit_current_agent()
        self._tools = tools
        return self
    
    def registry_url(self, registry_url: str) -> 'SandboxBuilder':
        """Set registry URL."""
        self._auto_commit_current_agent()
        self._registry_url = registry_url
        return self
    
    def custom_env_tools(self, custom_env_tools: Any) -> 'SandboxBuilder':
        """Set custom environment tools."""
        self._auto_commit_current_agent()
        self._custom_env_tools = custom_env_tools
        return self
    
    def agents(self, agents: Optional[Dict[str, Any]] = None) -> 'SandboxBuilder | AgentsBuilder':
        """Set agents configuration or return agents builder for chain building.
        
        Usage:
            # Direct assignment
            builder.agents(agent_config)
            
            # Chain building
            builder.agents().agent_1().run_mode("local").location("/path").build()
        """
        if agents is not None:
            self._agents = agents
            return self
        return self._agents_builder
    
    def _add_agent(self, name: str, config: Dict[str, Any]):
        """Internal method to add an agent configuration."""
        if self._agents is None:
            self._agents = {}
        self._agents[name] = config
    
    def build(self) -> 'Sandbox':
        """Build and return the Sandbox instance.
        This is the only build() call needed - all agent configurations are auto-committed.
        """
        # Auto-commit current agent if exists
        self._auto_commit_current_agent()
        
        # Import here to avoid circular import
        from aworld.sandbox import Sandbox
        
        kwargs = {}
        
        if self._sandbox_id is not None:
            kwargs['sandbox_id'] = self._sandbox_id
        if self._env_type is not None:
            kwargs['env_type'] = self._env_type
        if self._metadata is not None:
            kwargs['metadata'] = self._metadata
        if self._timeout is not None:
            kwargs['timeout'] = self._timeout
        if self._mcp_servers is not None:
            kwargs['mcp_servers'] = self._mcp_servers
        if self._mcp_config is not None:
            kwargs['mcp_config'] = self._mcp_config
        if self._black_tool_actions is not None:
            kwargs['black_tool_actions'] = self._black_tool_actions
        if self._skill_configs is not None:
            kwargs['skill_configs'] = self._skill_configs
        if self._tools is not None:
            kwargs['tools'] = self._tools
        if self._registry_url is not None:
            kwargs['registry_url'] = self._registry_url
        if self._custom_env_tools is not None:
            kwargs['custom_env_tools'] = self._custom_env_tools
        if self._agents is not None:
            kwargs['agents'] = self._agents
        
        return Sandbox(**kwargs)

