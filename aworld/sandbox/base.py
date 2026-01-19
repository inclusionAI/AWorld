import abc
import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Dict, List, Any, Optional

from aworld.logs.util import logger
from aworld.sandbox.api.setup import SandboxSetup
from aworld.sandbox.models import SandboxStatus, SandboxEnvType, SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers
from aworld.sandbox.utils.mcp_client import get_tools_from_mcp_servers
from aworld.sandbox.utils.util import is_url, process_registry_url, ensure_registry_file_exists



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
        """Returns the environment registry URL.
        
        Note: This returns the processed/expanded path, which may differ from the input:
        - Directory paths are expanded to full file paths (e.g., ~/workspace -> ~/workspace/registry.json)
        - Relative paths are resolved to absolute paths
        - User home paths (~) are expanded to full paths
        - URLs are returned as-is
        """
        return self._registry_url

    @property
    def custom_env_tools(self) -> Optional[Any]:
        """Returns the custom environment tools."""
        return self._custom_env_tools

    @property
    def reuse(self) -> bool:
        """Returns whether to reuse MCP server connections."""
        return self._reuse

    @reuse.setter
    def reuse(self, value: bool) -> None:
        """Set whether to reuse MCP server connections."""
        self._reuse = value

    @property
    def agents(self) -> Optional[Dict[str, Any]]:
        """Returns the custom environment agents.
        """
        return self._agents

    @property
    def streaming(self) -> bool:
        """Returns whether streaming is enabled for tool responses.

        Returns:
            bool: True if streaming is enabled, False otherwise.
        """
        return self._streaming

    @property
    def env_content_name(self) -> str:
        """Returns the environment content parameter name used in tool schemas.

        Returns:
            str: The parameter name (default: "env_content").
        """
        return self._env_content_name

    @env_content_name.setter
    def env_content_name(self, value: str):
        """Set environment content parameter name.

        Args:
            value: The parameter name to use in tool schemas.
        """
        self._env_content_name = value or "env_content"

    @property
    def env_content(self) -> Dict[str, Any]:
        """Returns the environment content values (user-defined context).

        This dictionary stores user-defined context parameters that will be
        automatically injected into tool calls. Note that task_id and session_id
        are added dynamically from context during tool calls.

        Returns:
            Dict[str, Any]: Dictionary of context values.
        """
        return self._env_content

    @env_content.setter
    def env_content(self, value: Dict[str, Any]):
        """Set environment content values.

        Args:
            value: Dictionary of user-defined context parameters.
        """
        self._env_content = value or {}

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
            agents: Optional[Dict[str, Any]] = None,
            streaming: bool = False,
            env_content_name: Optional[str] = None,
            env_content: Optional[Dict[str, Any]] = None,
            reuse: bool = False,
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
            reuse: Whether to reuse MCP server connections. Default is False (create new connection for each call).
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
        default_registry_url = os.getenv("ENV_REGISTRY_URL", "~/workspace/registry.json")
        self._registry_url = registry_url or default_registry_url
        self._custom_env_tools = custom_env_tools
        self._reuse = reuse
        self._agents = agents
        self._streaming = streaming
        # Environment content context for tool parameters
        self._env_content_name: str = env_content_name or "env_content"  # Parameter name in tool schema
        self._env_content: Dict[str, Any] = env_content or {}  # User-defined context values

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


    @staticmethod
    async def register(
        registry_url: Optional[str] = None,
        name: Optional[str] = None,
        version: Optional[str] = None,
        description: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        token: Optional[str] = None,
        servers: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        # Get default registry_url if not provided
        if not registry_url:
            registry_url = os.getenv("ENV_REGISTRY_URL", "~/workspace/registry.json")
        
        # Process registry_url (expand paths, handle directories, etc.)
        processed_registry_url = process_registry_url(registry_url)
        
        # Check if registry_url is a URL or local path
        if is_url(processed_registry_url):
            # Remote registration: validate required parameters
            if not all([name, version, description, data, tools]):
                return {
                    "success": False,
                    "message": "For remote registration, name, version, description, data, and tools are required."
                }
            return await Sandbox._register_to_remote(
                processed_registry_url, name, version, description, data, tools, token
            )
        else:
            # Local registration: validate required parameters
            if not servers:
                return {
                    "success": False,
                    "message": "For local registration, servers parameter is required."
                }
            return await Sandbox._register_to_local_from_servers(processed_registry_url, servers)

    @staticmethod
    async def _register_to_remote(
        registry_url: str,
        name: str,
        version: str,
        description: str,
        data: Dict[str, Any],
        tools: List[Dict[str, Any]],
        token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Register tool to remote registry."""
        if not token:
            token = os.getenv("REGISTRY_TOKEN", "")
            if not token:
                return {
                    "success": False,
                    "message": "Token is required for remote registration. Set REGISTRY_TOKEN environment variable or pass token parameter."
                }
        
        try:
            import httpx
            
            # Normalize registry_url: remove trailing slash
            base_url = registry_url.rstrip('/')
            
            # Check if base_url already contains /api/v1
            register_url = f"{base_url}/api/v1/registry/tool"
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            }
            
            request_data = {
                "name": name,
                "version": version,
                "description": description,
                "data": data,
                "tools": tools,
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(register_url, headers=headers, json=request_data)
                
                if response.status_code == 200:
                    result = response.json()
                    return {
                        "success": True,
                        "entity_id": result.get("entity_id", ""),
                        "message": f"Successfully registered {name}"
                    }
                else:
                    return {
                        "success": False,
                        "message": f"Registration failed with status {response.status_code}: {response.text}"
                    }
                    
        except ImportError:
            return {
                "success": False,
                "message": "httpx is not installed, cannot register to remote registry"
            }
        except Exception as e:
            logger.warning(f"Failed to register to remote registry: {e}")
            return {
                "success": False,
                "message": f"Registration failed: {str(e)}"
            }

    @staticmethod
    async def _register_to_local_from_servers(
        file_path: str,
        servers: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Register tools from MCP servers to local file."""
        try:
            # Expand user path (~/workspace -> /Users/username/workspace) before resolving
            path = Path(file_path).expanduser().resolve()
            
            # Create directory if it doesn't exist
            if path.parent != path:  # Not root directory
                path.parent.mkdir(parents=True, exist_ok=True)
            
            # Load existing registry data (dict format with entity_type+name as key)
            if path.exists():
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        registry_data = json.load(f)
                    # Validate that registry_data is a dict, if not, clear it
                    if not isinstance(registry_data, dict):
                        logger.warning(f"Registry file {path} is not a valid dict format, clearing it")
                        registry_data = {}
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Failed to parse registry file {path}: {e}, clearing it")
                    registry_data = {}
            else:
                registry_data = {}
            
            # Prepare mcp_config from servers
            mcp_config = {"mcpServers": servers.get("mcpServers", servers)}
            
            # Get server names
            server_names = list(mcp_config.get("mcpServers", {}).keys())
            
            if not server_names:
                return {
                    "success": False,
                    "message": "No servers found in servers configuration"
                }
            
            # Get tools from MCP servers
            # Returns dict: {server_name: [{"name": "...", "description": "..."}, ...]}
            server_tools_map = await get_tools_from_mcp_servers(
                mcp_config=mcp_config,
                server_names=server_names
            )
            
            # Register each server
            registered_count = 0
            for server_name in server_names:
                server_config = mcp_config.get("mcpServers", {}).get(server_name, {})
                server_tools = server_tools_map.get(server_name, [])
                
                # Create entity
                entity_key = f"tool:{server_name}"
                entity = {
                    "entity_type": "tool",
                    "name": server_name,
                    "description": f"{server_name} 服务工具",
                    "tools": server_tools,  # Already in format [{"name": "...", "description": "..."}, ...]
                    "data": server_config  # Store the complete server configuration
                }
                
                # Update or add entity (using entity_type+name as key)
                registry_data[entity_key] = entity
                registered_count += 1
                logger.info(f"Registered entity: {entity_key} with {len(server_tools)} tools")
            
            # Write back to file
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(registry_data, f, ensure_ascii=False, indent=2)
            
            return {
                "success": True,
                "file_path": str(path.absolute()),
                "message": f"Successfully registered {registered_count} server(s) to local file",
                "registered_count": registered_count
            }
            
        except Exception as e:
            logger.warning(f"Failed to register to local file: {e}")
            import traceback
            logger.warning(traceback.format_exc())
            return {
                "success": False,
                "message": f"Registration failed: {str(e)}"
            }

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
