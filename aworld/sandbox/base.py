import abc
import asyncio
import logging
import os
import uuid
from typing import Dict, List, Any, Optional

from aworld.logs.util import logger
from aworld.sandbox.api.setup import SandboxSetup
from aworld.sandbox.models import SandboxStatus, SandboxEnvType, SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers


class Sandbox(SandboxSetup):
    """
    Sandbox abstract base class that defines the interface for all sandbox implementations.
    A sandbox provides an isolated environment for executing code and operations.
    """

    default_sandbox_timeout = 3000

    @property
    def sandbox_id(self) -> str:
        """
        Returns the unique identifier of the sandbox.
        """
        return self._sandbox_id

    @property
    def status(self) -> SandboxStatus:
        """
        Returns the current status of the sandbox.
        """
        return self._status

    @property
    def timeout(self) -> int:
        """
        Returns the timeout value for sandbox operations.
        """
        return self._timeout

    @property
    def metadata(self) -> Dict[str, Any]:
        """
        Returns the sandbox metadata.
        """
        return self._metadata

    @property
    def env_type(self) -> SandboxEnvType:
        """
        Returns the environment type of the sandbox.
        """
        return self._env_type

    @property
    def mcp_config(self) -> Any:
        """Returns the MCP configuration."""
        return self._mcp_config

    @property
    def skill_configs(self) -> Any:
        """Returns the MCP configuration."""
        return self._skill_configs

    @property
    def mcp_servers(self) -> List[str]:
        """Returns the list of MCP servers."""
        return self._mcp_servers

    @property
    def black_tool_actions(self) -> Dict[str, List[str]]:
        """Returns the list of black-listed tools."""
        return self._black_tool_actions

    @property
    def tools(self) -> List[str]:
        """Returns the list of tools."""
        return self._tools

    @property
    def registry_url(self) -> str:
        """Returns the environment registry URL."""
        return self._registry_url

    @property
    def custom_env_tools(self) -> Optional[Any]:
        """Returns the custom environment tools."""
        return self._custom_env_tools

    @property
    @abc.abstractmethod
    def mcpservers(self) -> McpServers:
        """Module for running MCP in the sandbox.
        
        Returns:
            McpServers: The MCP servers instance.
        """
        pass

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
    ):
        """Initialize a new Sandbox instance.
        
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
        """
        # Initialize basic attributes
        self._sandbox_id = sandbox_id or str(uuid.uuid4())
        self._status = SandboxStatus.INIT
        self._timeout = timeout or self.default_sandbox_timeout
        self._metadata = metadata or {}
        self._env_type = env_type or SandboxEnvType.LOCAL
        self._mcp_servers = mcp_servers or []
        self._mcp_config = mcp_config or {}
        self._skill_configs = skill_configs or {}
        self._black_tool_actions = black_tool_actions or {}
        self._tools = tools or []
        # Read registry_url from environment variable if not provided
        self._registry_url = registry_url or os.getenv("ENV_REGISTRY_URL", "")
        self._custom_env_tools = custom_env_tools

    @abc.abstractmethod
    def get_info(self) -> SandboxInfo:
        """Returns information about the sandbox.
        
        Returns:
            SandboxInfo: Information about the sandbox.
        """
        pass

    @abc.abstractmethod
    async def remove(self) -> bool:
        """Remove the sandbox and clean up all resources.
        
        Returns:
            bool: True if removal was successful, False otherwise.
        """
        pass

    @abc.abstractmethod
    def get_skill_list(self) -> Optional[Any]:
        """Get the skill configurations.
        
        Returns:
            Optional[Any]: The skill configurations, or None if empty.
        """
        pass

    @abc.abstractmethod
    async def cleanup(self) -> bool:
        """Clean up the sandbox resources.
        
        Returns:
            bool: True if cleanup was successful, False otherwise.
        """
        pass

    async def list_tools(self, context: Any = None) -> List[Dict[str, Any]]:
        """
        List all available tools from MCP servers.
        This is a convenience method that delegates to mcpservers.list_tools().
        
        Args:
            context: Optional context object.
        
        Returns:
            List of tool descriptions.
        """
        # This method is implemented in BaseSandbox
        # Defined here for type hints and IDE autocomplete
        if hasattr(self, 'mcpservers') and self.mcpservers is not None:
            return await self.mcpservers.list_tools(context=context)
        return []

    async def call_tool(
        self,
        action_list: List[Dict[str, Any]] = None,
        task_id: str = None,
        session_id: str = None,
        context: Any = None
    ) -> List[Any]:
        """
        Call a tool on MCP servers.
        This is a convenience method that delegates to mcpservers.call_tool().
        
        Args:
            action_list: List of actions to execute.
            task_id: Optional task ID.
            session_id: Optional session ID.
            context: Optional context object.
        
        Returns:
            List of action results.
        """
        # This method is implemented in BaseSandbox
        # Defined here for type hints and IDE autocomplete
        if hasattr(self, 'mcpservers') and self.mcpservers is not None:
            return await self.mcpservers.call_tool(
                action_list=action_list,
                task_id=task_id,
                session_id=session_id,
                context=context
            )
        return []

    def __del__(self):
        """Ensure resources are cleaned up when the object is garbage collected."""
        # NOTE: use logging in __del__ for log
        try:
            # Handle the case where an event loop already exists
            try:
                loop = asyncio.get_running_loop()
                logging.warning("Cannot clean up sandbox in __del__ when event loop is already running")
                return
            except RuntimeError:
                # No running event loop, create a new one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.cleanup())
                loop.close()
                logging.warning(f"cleanup sandbox resources during garbage collection: {id(asyncio.get_running_loop())}")
        except Exception as e:
            logging.debug(f"Failed to cleanup sandbox resources during garbage collection: {e}")
