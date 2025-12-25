import abc
from typing import Dict, List, Any, Optional

from aworld.logs.util import logger
from aworld.sandbox.base import Sandbox
from aworld.sandbox.models import SandboxStatus, SandboxEnvType, SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers


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
                        "type": "local",  # optional: "local" or "remote" (case-insensitive), default is "local"
                        "env": {"KEY": "value"},  # optional
                        "args": ["--option"],  # optional
                        # ... other optional config
                    }
                }
                
                Note: If "type" is provided, it will be used directly (case-insensitive).
                      If "type" is not provided, the function will auto-detect based on location.
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
            agents=agents
        )
        self._logger = self._setup_logger()
        # Track if sandbox has been initialized (for lazy initialization support)
        self._initialized = False
    
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
    
    @abc.abstractmethod
    async def remove(self) -> bool:
        """
        Remove the sandbox.
        This method must be implemented by subclasses to provide environment-specific removal.
        
        Returns:
            bool: True if removal was successful, False otherwise.
        """
        pass 