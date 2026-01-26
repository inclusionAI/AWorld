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
        self._streaming: bool = False
        self._env_content_name: Optional[str] = None
        self._env_content: Optional[Dict[str, Any]] = None
        self._workspace: Optional[List[str]] = None
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
    
    def streaming(self, streaming: bool) -> 'SandboxBuilder':
        """Set streaming mode for tool responses.
        
        Args:
            streaming: Whether to enable streaming for tool responses.
        
        Returns:
            SandboxBuilder: Self for method chaining.
        """
        self._streaming = streaming
        return self
    
    def env_content_name(self, env_content_name: str) -> 'SandboxBuilder':
        """Set environment content parameter name.
        
        Args:
            env_content_name: Parameter name for environment content in tool schemas.
        
        Returns:
            SandboxBuilder: Self for method chaining.
        """
        self._env_content_name = env_content_name
        return self
    
    def env_content(self, env_content: Dict[str, Any]) -> 'SandboxBuilder':
        """Set environment content values.
        
        Args:
            env_content: User-defined context values to be automatically injected into tool calls.
        
        Returns:
            SandboxBuilder: Self for method chaining.
        """
        self._env_content = env_content
        return self
    
    def workspace(self, workspace: List[str]) -> 'SandboxBuilder':
        """Set workspace directories for filesystem tool.
        
        Args:
            workspace: List of allowed workspace directory paths. If None, uses default workspaces 
                (~/workspace, ~/aworld_workspace). Can also be set via environment variable 
                AWORLD_WORKSPACE_PATH (comma-separated paths).
        
        Returns:
            SandboxBuilder: Self for method chaining.
        
        Examples:
            # Single workspace
            builder.workspace(["~/workspace"])
            
            # Multiple workspaces
            builder.workspace(["~/workspace", "~/projects", "/custom/path"])
        """
        self._auto_commit_current_agent()
        self._workspace = workspace
        return self
    
    def _add_agent(self, name: str, config: Dict[str, Any]):
        """Internal method to add an agent configuration."""
        if self._agents is None:
            self._agents = {}
        self._agents[name] = config

    # ==================== Builtin tools proxies (for IDE completion) ====================

    async def read_file(self, path: str, head: Optional[int] = None, tail: Optional[int] = None) -> str:
        """Proxy to Sandbox.read_file for IDE completion."""
        instance = self.build()
        return await instance.read_file(path=path, head=head, tail=tail)

    async def write_file(self, path: str, content: str) -> str:
        """Proxy to Sandbox.write_file for IDE completion."""
        instance = self.build()
        return await instance.write_file(path=path, content=content)

    async def edit_file(self, path: str, edits: List[dict], dryRun: bool = False) -> str:
        """Proxy to Sandbox.edit_file for IDE completion."""
        instance = self.build()
        return await instance.edit_file(path=path, edits=edits, dryRun=dryRun)

    async def create_directory(self, path: str) -> str:
        """Proxy to Sandbox.create_directory for IDE completion."""
        instance = self.build()
        return await instance.create_directory(path=path)

    async def list_directory(self, path: str) -> str:
        """Proxy to Sandbox.list_directory for IDE completion."""
        instance = self.build()
        return await instance.list_directory(path=path)

    async def move_file(self, source: str, destination: str) -> str:
        """Proxy to Sandbox.move_file for IDE completion."""
        instance = self.build()
        return await instance.move_file(source=source, destination=destination)

    async def list_allowed_directories(self) -> str:
        """Proxy to Sandbox.list_allowed_directories for IDE completion."""
        instance = self.build()
        return await instance.list_allowed_directories()

    async def run_code(self, code: str, timeout: int = 30, output_format: str = "markdown") -> str:
        """Proxy to Sandbox.run_code for IDE completion."""
        instance = self.build()
        return await instance.run_code(code=code, timeout=timeout, output_format=output_format)

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
        if self._streaming is not False:  # Only add if explicitly set to True
            kwargs['streaming'] = self._streaming
        if self._env_content_name is not None:
            kwargs['env_content_name'] = self._env_content_name
        if self._env_content is not None:
            kwargs['env_content'] = self._env_content
        if self._workspace is not None:
            kwargs['workspace'] = self._workspace
        
        # Ensure at least mcp_config is provided to avoid Sandbox() returning Builder
        # mcp_config defaults to {} in Sandbox.__init__ if not provided
        if 'mcp_config' not in kwargs:
            kwargs['mcp_config'] = {}
        
        return Sandbox(**kwargs)
    
    def __getattr__(self, name: str):
        """Auto-build and forward attribute access to Sandbox instance.
        
        This allows Sandbox() to be used directly without calling .build(),
        while still supporting the Builder pattern for chain calls.
        
        Args:
            name: Attribute name to access
            
        Returns:
            Attribute from built Sandbox instance
        """
        # Builder methods - return them normally
        builder_methods = {
            'build', 'sandbox_id', 'env_type', 'metadata', 'timeout',
            'mcp_servers', 'mcp_config', 'black_tool_actions', 'skill_configs',
            'tools', 'registry_url', 'custom_env_tools', 'agents', 'streaming',
            'env_content_name', 'env_content', 'workspace', '_auto_commit_current_agent',
            '_add_agent', '_agents_builder'
        }
        
        # If it's a Builder method or private attribute, use normal attribute access
        if name.startswith('_') or name in builder_methods:
            return object.__getattribute__(self, name)
        
        # For any other attribute/method (Sandbox methods), auto-build and forward
        instance = self.build()
        return getattr(instance, name)

