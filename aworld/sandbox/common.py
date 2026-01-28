import abc
from typing import Dict, List, Any, Optional

from aworld.logs.util import logger
from aworld.sandbox.base import Sandbox
from aworld.sandbox.models import SandboxStatus, SandboxEnvType, SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers
from aworld.sandbox.builtin import (
    FilesystemTool,
    TerminalTool,
    BuiltinToolRouter,
    SERVICE_FILESYSTEM,
    SERVICE_TERMINAL,
    builtin_tool,
)


class BaseSandbox(Sandbox):
    """
    Base sandbox implementation with common functionality for all sandbox types.
    This class implements common methods and provides a foundation for specific sandbox implementations.
    """

    def __init__(
        self,
        sandbox_id: Optional[str] = None,
        env_type: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        mcp_servers: Optional[List[str]] = None,
        mcp_config: Optional[Any] = None,
        black_tool_actions: Optional[Dict[str, List[str]]] = None,
        skill_configs: Optional[Any] = None,
        tools: Optional[List[str]] = None,
        registry_url: Optional[str] = None,
        custom_env_tools: Optional[Any] = None,
        agents: Optional[Dict[str, Any]] = None,
        streaming: bool = False,
        env_content_name: Optional[str] = None,
        env_content: Optional[Dict[str, Any]] = None,
        reuse: bool = False,
        workspace: Optional[List[str]] = None,
        mode: str = "local",
    ):
        """
        Initialize a new BaseSandbox instance.
        
        Args:
            sandbox_id: Unique identifier for the sandbox. If None, one will be generated.
            env_type: The environment type (LOCAL, K8S, SUPERCOMPUTER).
            metadata: Additional metadata for the sandbox.
            timeout: Timeout for sandbox operations.
            mcp_servers: List of MCP servers to use.
            mcp_config: Configuration for MCP servers.
            black_tool_actions: Black list of tool actions.
            skill_configs: Skill configurations.
            tools: List of tools. Optional parameter.
            registry_url: Environment registry URL. Optional parameter, reads from environment variable "ENV_REGISTRY_URL" if not provided, defaults to empty string.
            custom_env_tools: Custom environment tools. Optional parameter.
            agents: Custom environment agents. Optional parameter.
                Supports two formats (mixed mode):

                Simple format (auto-detected):
                {
                    "local_agent": "/path/to/agent.py",
                    "remote_agent": "https://github.com/..."
                }

                Extended format (with additional config):
                {
                    "advanced_agent": {
                        "location": "/path/to/agent.py",  # or "https://..."
                        "run_mode": "local",  # optional: "local" or "remote" (case-insensitive), default is "local"
                        "env": {"KEY": "value"},  # optional
                        "args": ["--option"],  # optional
                        # ... other optional config
                    }
                }

                Note: If "type" is provided, it will be used directly (case-insensitive).
                      If "type" is not provided, the function will auto-detect based on location.
            streaming: Whether to enable streaming for tool responses. Defaults to False.
            env_content_name: Parameter name for environment content in tool schemas. Defaults to "env_content".
            env_content: User-defined context values to be automatically injected into tool calls.
                Note that task_id and session_id are added dynamically from context during tool calls.
            reuse: Whether to reuse MCP server connections. Default is False.
            workspace: List of allowed workspace directories for filesystem tool. If None, uses default workspaces 
                (~/workspace, ~/aworld_workspace). Can also be set via environment variable AWORLD_WORKSPACE_PATH 
                (comma-separated paths).
        """
        super().__init__(
            sandbox_id=sandbox_id,
            env_type=env_type,
            metadata=metadata,
            timeout=timeout,
            mcp_servers=mcp_servers,
            mcp_config=mcp_config,
            black_tool_actions=black_tool_actions,
            skill_configs=skill_configs,
            tools=tools,
            registry_url=registry_url,
            custom_env_tools=custom_env_tools,
            agents=agents,
            streaming=streaming,
            env_content_name=env_content_name,
            env_content=env_content,
            reuse=reuse,
            workspace=workspace,
            mode=mode,
        )
        self._logger = self._setup_logger()
        # Track if sandbox has been initialized (for lazy initialization support)
        self._initialized = False
        
        # Initialize builtin tools with workspace configuration
        self._builtin_filesystem = FilesystemTool(allowed_directories=self._workspace)
        logger.debug(f"Initialized FilesystemTool with workspace: {self._workspace} (will use defaults if None)")
        self._builtin_terminal = TerminalTool()
        self._tool_router = BuiltinToolRouter(self)
        
        # Register builtin tool methods
        self._register_builtin_tools()

    def _trigger_reinitialize(self, reinit_type: str = "mcpservers", attribute_name: str = ""):
        """
        Helper method to trigger reinitialization based on type.

        Args:
            reinit_type: Type of reinitialization - "mcpservers" (lightweight) or "full" (complete)
            attribute_name: Name of the attribute being changed (for error messages)
        """
        if reinit_type == "mcpservers":
            # Lightweight reinitialization: only recreate MCP servers
            if hasattr(self, '_reinitialize_mcpservers'):
                # _reinitialize_mcpservers handles both initialized and uninitialized cases
                self._reinitialize_mcpservers()
        elif reinit_type == "full":
            # Full reinitialization: reparse config, query registry, etc.
            if hasattr(self, '_initialize_sandbox'):
                try:
                    self._initialize_sandbox()
                except Exception as e:
                    logger.warning(f"Failed to reinitialize sandbox after {attribute_name} change: {e}")

    def _setup_logger(self):
        """
        Set up a logger for the sandbox instance.
        
        Returns:
            logger.Logger: Configured logger instance.
        """
        return logger
        
    def get_info(self) -> SandboxInfo:
        """
        Get information about the sandbox.
        
        Returns:
            SandboxInfo: Information about the sandbox.
        """
        return {
            "sandbox_id": self.sandbox_id,
            "status": self.status,
            "metadata": self.metadata,
            "env_type": self.env_type
        }
    
    @property
    def mcpservers(self) -> McpServers:
        """
        Module for running MCP servers in the sandbox.
        This property provides access to the MCP servers instance.
        If sandbox is not initialized yet, it will attempt to initialize automatically.

        Returns:
            McpServers: The MCP servers instance, or None if not initialized.
        """
        if hasattr(self, '_mcpservers') and self._mcpservers is not None:
            return self._mcpservers
        # If not initialized and has configuration, try to initialize
        if not self._initialized and (self._mcp_config or self._mcp_servers):
            if hasattr(self, '_initialize_sandbox'):
                try:
                    self._initialize_sandbox()
                    return self._mcpservers if hasattr(self, '_mcpservers') else None
                except Exception as e:
                    logger.warning(f"Failed to auto-initialize sandbox: {e}")
        return None
    
    @property
    def mcp_config(self) -> Any:
        """Returns the MCP configuration."""
        return self._mcp_config

    @mcp_config.setter
    def mcp_config(self, value: Any):
        """Set MCP configuration and reinitialize if needed."""
        self._mcp_config = value or {}
        self._trigger_reinitialize("mcpservers", "mcp_config")

    @property
    def black_tool_actions(self) -> Dict[str, List[str]]:
        """Returns the list of black-listed tools."""
        return self._black_tool_actions

    @black_tool_actions.setter
    def black_tool_actions(self, value: Dict[str, List[str]]):
        """Set black tool actions and reinitialize if needed."""
        self._black_tool_actions = value or {}
        self._trigger_reinitialize("mcpservers", "black_tool_actions")

    @property
    def mcp_servers(self) -> List[str]:
        """Returns the list of MCP servers."""
        return self._mcp_servers

    @mcp_servers.setter
    def mcp_servers(self, value: List[str]):
        """Set MCP servers list and reinitialize if needed."""
        self._mcp_servers = value or []
        self._trigger_reinitialize("mcpservers", "mcp_servers")

    @property
    def skill_configs(self) -> Any:
        """Returns the skill configurations."""
        return self._skill_configs

    @skill_configs.setter
    def skill_configs(self, value: Any):
        """Set skill configurations and reinitialize if needed."""
        self._skill_configs = value or {}
        self._trigger_reinitialize("mcpservers", "skill_configs")

    @property
    def tools(self) -> List[str]:
        """Returns the list of tools."""
        return self._tools

    @tools.setter
    def tools(self, value: List[str]):
        """Set tools list. May trigger reinitialization if sandbox is already initialized."""
        self._tools = value or []
        self._trigger_reinitialize("full", "tools")

    @property
    def registry_url(self) -> str:
        """Returns the environment registry URL."""
        return self._registry_url

    @registry_url.setter
    def registry_url(self, value: str):
        """Set registry URL. May trigger reinitialization if sandbox is already initialized."""
        self._registry_url = value or ""
        self._trigger_reinitialize("full", "registry_url")

    @property
    def custom_env_tools(self) -> Optional[Any]:
        """Returns the custom environment tools."""
        return self._custom_env_tools

    @custom_env_tools.setter
    def custom_env_tools(self, value: Optional[Any]):
        """Set custom environment tools. May trigger reinitialization if sandbox is already initialized."""
        self._custom_env_tools = value
        self._trigger_reinitialize("mcpservers", "custom_env_tools")

    @property
    def agents(self) -> Optional[Dict[str, Any]]:
        """Returns the custom environment agents."""
        return self._agents

    @agents.setter
    def agents(self, value: Optional[Dict[str, Any]]):
        """Set custom environment agents. May trigger reinitialization if sandbox is already initialized."""
        self._agents = value
        self._trigger_reinitialize("mcpservers", "agents")

    @property
    def metadata(self) -> Dict[str, Any]:
        """Returns the sandbox metadata."""
        return self._metadata

    @metadata.setter
    def metadata(self, value: Dict[str, Any]):
        """Set sandbox metadata."""
        self._metadata = value or {}

    @property
    def timeout(self) -> int:
        """Returns the timeout value for sandbox operations."""
        return self._timeout

    @timeout.setter
    def timeout(self, value: int):
        """Set timeout value for sandbox operations."""
        self._timeout = value or self.default_sandbox_timeout

    @property
    def env_content_name(self) -> str:
        """Returns the environment content parameter name used in tool schemas."""
        return self._env_content_name

    @env_content_name.setter
    def env_content_name(self, value: str):
        """Set environment content parameter name and reinitialize if needed.

        Changing env_content_name requires reprocessing tool schemas to remove
        the old parameter name and handle the new one.
        """
        old_value = self._env_content_name
        self._env_content_name = value or "env_content"
        # If the name changed and sandbox is initialized, need to reinitialize
        # to reprocess tool schemas with the new parameter name
        if old_value != self._env_content_name and self._initialized:
            self._trigger_reinitialize("mcpservers", "env_content_name")

    @property
    def env_content(self) -> Dict[str, Any]:
        """Returns the environment content values (user-defined context)."""
        return self._env_content

    @env_content.setter
    def env_content(self, value: Dict[str, Any]):
        """Set environment content values.

        Changing env_content only affects the values injected during tool calls,
        it does not affect tool schemas, so no reinitialization is needed.
        """
        self._env_content = value or {}

    @property
    def workspace(self) -> Optional[List[str]]:
        """Returns the workspace directories for filesystem tool."""
        return self._workspace

    @workspace.setter
    def workspace(self, value):
        """Set workspace directories for filesystem tool and reinitialize if needed.
        
        Changing workspace requires reinitializing FilesystemTool to update allowed directories.
        
        Args:
            value: List of allowed workspace directory paths, or a single string path.
                   If None, uses default workspaces.
        """
        # Convert string to list for convenience
        if isinstance(value, str):
            value = [value]
        elif value is not None and not isinstance(value, list):
            raise TypeError(f"workspace must be a list of strings or a single string, got {type(value)}")
        
        old_value = self._workspace
        self._workspace = value
        # If workspace changed and sandbox is initialized, update FilesystemTool
        if old_value != self._workspace:
            if hasattr(self, '_builtin_filesystem') and self._builtin_filesystem:
                # Update existing FilesystemTool instance instead of recreating
                self._builtin_filesystem.update_allowed_directories(self._workspace)
                logger.info(f"Updated FilesystemTool workspace to: {self._workspace}")
            elif self._initialized:
                # If FilesystemTool doesn't exist yet but sandbox is initialized, create it
                self._builtin_filesystem = FilesystemTool(allowed_directories=self._workspace)
                logger.info(f"Initialized FilesystemTool with workspace: {self._workspace}")

    @abc.abstractmethod
    def get_skill_list(self) -> Optional[Any]:
        """
        Get the skill configurations.
        This method must be implemented by subclasses.
        
        Returns:
            Optional[Any]: The skill configurations, or None if empty.
        """
        pass
    
    @abc.abstractmethod
    async def cleanup(self) -> bool:
        """
        Clean up sandbox resources.
        This method must be implemented by subclasses to provide environment-specific cleanup.
        
        Returns:
            bool: True if cleanup was successful, False otherwise.
        """
        pass
    
    def _register_builtin_tools(self):
        """Register builtin tool methods with routing support."""
        # Wrap all builtin tool methods
        for attr_name in dir(self):
            if attr_name.startswith('_'):
                continue
            attr = getattr(self, attr_name)
            if hasattr(attr, '_is_builtin_tool'):
                # Replace with wrapped method
                setattr(self, attr_name, self._create_tool_wrapper(attr))
    
    def _create_tool_wrapper(self, original_method):
        """Create a wrapper for builtin tool methods that routes to MCP or builtin implementation."""
        import inspect
        
        # Get method signature to convert *args to **kwargs
        sig = inspect.signature(original_method)
        param_names = list(sig.parameters.keys())
        # Skip 'self' parameter
        if param_names and param_names[0] == 'self':
            param_names = param_names[1:]
        
        async def wrapper(*args, **kwargs):
            service_name = original_method._service_name
            tool_name = original_method._tool_name
            
            # Convert *args to **kwargs based on method signature
            # This ensures positional arguments are properly passed to builtin_impl.execute
            for i, arg_value in enumerate(args):
                if i < len(param_names):
                    param_name = param_names[i]
                    if param_name not in kwargs:  # Don't override if already in kwargs
                        kwargs[param_name] = arg_value
            
            # Get builtin implementation
            if service_name == SERVICE_FILESYSTEM:
                builtin_impl = self._builtin_filesystem
            elif service_name == SERVICE_TERMINAL:
                builtin_impl = self._builtin_terminal
            else:
                # Fallback to original method if service not recognized
                return await original_method(self, *args, **kwargs)
            
            # Route the call (now all args are in kwargs)
            return await self._tool_router.route_call(
                service_name=service_name,
                tool_name=tool_name,
                builtin_impl=builtin_impl,
                **kwargs
            )
        
        # Copy metadata from original method
        wrapper.__name__ = original_method.__name__
        wrapper.__doc__ = original_method.__doc__
        return wrapper
    
    # ==================== Filesystem Builtin Tools ====================
    
    @builtin_tool(service=SERVICE_FILESYSTEM, tool_name="read_file")
    async def read_file(self, path: str, head: Optional[int] = None, tail: Optional[int] = None) -> str:
        """Read text file content.
        
        Args:
            path: File path to read
            head: Return only first N lines
            tail: Return only last N lines
            
        Returns:
            File content as string
        """
        # This method will be wrapped by _create_tool_wrapper
        # The actual implementation is in FilesystemTool
        pass
    
    @builtin_tool(service=SERVICE_FILESYSTEM, tool_name="write_file")
    async def write_file(self, path: str, content: str) -> str:
        """Create or overwrite a file.
        
        Args:
            path: File path to write
            content: File content
            
        Returns:
            Success message
        """
        pass
    
    @builtin_tool(service=SERVICE_FILESYSTEM, tool_name="edit_file")
    async def edit_file(self, path: str, edits: List[dict], dryRun: bool = False) -> str:
        """Edit file with text replacements.
        
        Args:
            path: File path to edit
            edits: List of edit operations with oldText and newText
            dryRun: Preview changes without applying
            
        Returns:
            Diff text showing changes
        """
        pass
    
    @builtin_tool(service=SERVICE_FILESYSTEM, tool_name="create_directory")
    async def create_directory(self, path: str) -> str:
        """Create directory.
        
        Args:
            path: Directory path to create
            
        Returns:
            Success message
        """
        pass
    
    @builtin_tool(service=SERVICE_FILESYSTEM, tool_name="list_directory")
    async def list_directory(self, path: str) -> str:
        """List directory contents.
        
        Args:
            path: Directory path to list
            
        Returns:
            Directory listing as string
        """
        pass
    
    @builtin_tool(service=SERVICE_FILESYSTEM, tool_name="move_file")
    async def move_file(self, source: str, destination: str) -> str:
        """Move or rename file.
        
        Args:
            source: Source path
            destination: Destination path
            
        Returns:
            Success message
        """
        pass
    
    @builtin_tool(service=SERVICE_FILESYSTEM, tool_name="list_allowed_directories")
    async def list_allowed_directories(self) -> str:
        """List allowed directories.
        
        Returns:
            List of allowed directories
        """
        pass
    
    # ==================== Terminal Builtin Tools ====================
    
    @builtin_tool(service=SERVICE_TERMINAL, tool_name="run_code")
    async def run_code(self, code: str, timeout: int = 30, output_format: str = "markdown") -> str:
        """Execute terminal command or code.
        
        Args:
            code: Terminal command or code to execute
            timeout: Command timeout in seconds (default: 30)
            output_format: Output format: 'markdown', 'json', or 'text'
            
        Returns:
            Formatted command execution result
        """
        pass
    
    @abc.abstractmethod
    async def remove(self) -> bool:
        """
        Remove the sandbox.
        This method must be implemented by subclasses to provide environment-specific removal.
        
        Returns:
            bool: True if removal was successful, False otherwise.
        """
        pass 