import asyncio
import copy
import json
import os
import uuid
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict

from aworld.logs.util import logger
from aworld.sandbox.api.local.sandbox_api import LocalSandboxApi
from aworld.sandbox.models import SandboxStatus, SandboxEnvType, SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers
from aworld.sandbox.common import BaseSandbox
from aworld.utils.common import sync_exec


class LocalSandbox(BaseSandbox, LocalSandboxApi):
    """
    Local sandbox implementation that runs in the local environment.
    This sandbox runs processes and MCP servers directly on the local machine.
    """

    def __init__(
            self,
            sandbox_id: Optional[str] = None,
            metadata: Optional[Dict[str, str]] = None,
            timeout: Optional[int] = None,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Optional[Any] = None,
            black_tool_actions: Optional[Dict[str, List[str]]] = None,
            skill_configs: Optional[Any] = None,
            tools: Optional[List[str]] = None,
            registry_url: Optional[str] = None,
            custom_env_tools: Optional[Any] = None,
            **kwargs
    ):
        """Initialize a new LocalSandbox instance.
        
        Args:
            sandbox_id: Unique identifier for the sandbox. If None, one will be generated.
            metadata: Additional metadata for the sandbox.
            timeout: Timeout for sandbox operations.
            mcp_servers: List of MCP servers to use.
            mcp_config: Configuration for MCP servers.
            black_tool_actions: Black list of tool actions.
            skill_configs: Skill configurations.
            tools: List of tools. Optional parameter.
            registry_url: Environment registry URL. Optional parameter, reads from environment variable "ENV_REGISTRY_URL" if not provided, defaults to empty string.
            custom_env_tools: Custom environment tools. Optional parameter.
            **kwargs: Additional parameters for specific sandbox types.
        """
        super().__init__(
            sandbox_id=sandbox_id,
            env_type=SandboxEnvType.LOCAL,
            metadata=metadata,
            timeout=timeout,
            mcp_servers=mcp_servers,
            mcp_config=mcp_config,
            black_tool_actions=black_tool_actions,
            skill_configs=skill_configs,
            tools=tools,
            registry_url=registry_url,
            custom_env_tools=custom_env_tools
        )

        # Initialize properties
        self._status = SandboxStatus.INIT
        self._timeout = timeout or self.default_sandbox_timeout
        self._metadata = metadata or {}
        self._env_type = SandboxEnvType.LOCAL
        self._mcp_servers = mcp_servers
        self._mcp_config = mcp_config
        # Keep original logic: if mcp_config exists and mcp_servers is empty, populate from mcp_config
        if mcp_config and not mcp_servers:
            mcp_servers = list(mcp_config.get("mcpServers", {}).keys())
            self._mcp_servers = mcp_servers
        self._skill_configs = skill_configs
        self._black_tool_actions = black_tool_actions or {}

        # Resolve MCP configuration based on priority: tools > mcp_servers > mcp_config
        tools = tools or []
        registry_url = self.registry_url or ""

        # Step 1: Backup local mcp_config
        local_mcp_config = copy.deepcopy(mcp_config) if mcp_config else {}
        if "mcpServers" not in local_mcp_config:
            local_mcp_config["mcpServers"] = {}

        # Step 2: Extract local mcp_servers from mcp_config
        local_mcp_servers = [s for s in (mcp_servers or []) if s in local_mcp_config.get("mcpServers", {})] if mcp_config else []

        # Step 3: Resolve configuration from registry (without merging)
        # Use sync_exec to handle both sync and async contexts
        result = sync_exec(
            self._resolve_mcp_configuration, tools, mcp_servers or [], local_mcp_config, local_mcp_servers, registry_url
        )
        if result is None:
            final_mcp_servers = local_mcp_servers
            final_mcp_config = local_mcp_config
        else:
            final_mcp_servers, final_mcp_config = result

        # Step 4: Convert custom_env_tools to MCP config and merge
        if custom_env_tools:
            custom_mcp_config = self._convert_custom_env_tools_to_mcp_config(custom_env_tools)
            if custom_mcp_config:
                # Merge custom_env_tools config into final_mcp_config
                for server_name, server_config in custom_mcp_config.get("mcpServers", {}).items():
                    if server_name not in final_mcp_config.get("mcpServers", {}):
                        final_mcp_servers.append(server_name)
                    if "mcpServers" not in final_mcp_config:
                        final_mcp_config["mcpServers"] = {}
                    final_mcp_config["mcpServers"][server_name] = server_config

        self._mcp_servers = final_mcp_servers
        self._mcp_config = final_mcp_config

        # If no sandbox_id provided, create a new sandbox
        response = self._create_sandbox(
            env_type=self._env_type,
            env_config=None,
            mcp_servers=final_mcp_servers,
            mcp_config=final_mcp_config,
            black_tool_actions=black_tool_actions,
            skill_configs=skill_configs,
            sandbox_id=self.sandbox_id,  # Pass the ID generated by base class
        )

        if not response:
            self._status = SandboxStatus.ERROR
            # If creation fails, keep the generated UUID as the ID
            logger.warning(f"Failed to create sandbox, using generated ID: {self.sandbox_id}")
        else:
            # response.sandbox_id is now the same as self._sandbox_id, so no need to overwrite
            # self._sandbox_id = response.sandbox_id
            self._status = SandboxStatus.RUNNING
            self._metadata = {
                "status": getattr(response, 'status', None),
                "mcp_config": getattr(response, 'mcp_config', None),
                "env_type": getattr(response, 'env_type', None),
            }
            self._mcp_config = getattr(response, 'mcp_config', None)
            self._skill_configs = getattr(response, 'skill_configs', None)

        # Initialize McpServers with a reference to this sandbox instance
        self._mcpservers = McpServers(
            mcp_servers=final_mcp_servers,
            mcp_config=final_mcp_config,
            sandbox=self,
            black_tool_actions=self._black_tool_actions,
            skill_configs=self._skill_configs,
            tool_actions=tools
        )

    async def fetch_config_from_registry(
        self,
        registry_url: str,
        tools: Optional[List[str]] = None,
        servers: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Fetch raw data from registry center.
        This method only fetches data without processing. The data processing is done in merge_registry_configs.
        This method can be overridden by external implementations to use custom registry centers.
        
        Args:
            registry_url: Registry center URL
            tools: List of tool names to find corresponding servers (when tools is provided)
            servers: List of server names to fetch configurations (when servers is provided)
        
        Returns:
            Dict with structure: {
                "entities": [...]  # Raw entities from registry response
            }
        """
        if not registry_url:
            return {"entities": []}

        try:
            import httpx
            
            # Build search URL
            search_url = f"{registry_url.rstrip('/')}/api/v1/registry/search"
            
            # Build request payload
            payload = {
                "entity_type": "tool",
                "status": "active"
            }
            
            if tools:
                # Use tool names directly (registry accepts both formats: "server_name__tool_name" or "tool_name")
                payload["tools"] = tools
            
            if servers:
                payload["name"] = servers
            
            # Make async HTTP request
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(search_url, json=payload)
                
                # Check response status
                if response.status_code != 200:
                    error_text = response.text
                    logger.warning(f"Registry search failed with status {response.status_code}: {error_text}")
                    return {"entities": []}
                
                # Parse JSON response
                data = response.json()
                entities = data.get("entities", [])
                
                return {"entities": entities}
                    
        except ImportError:
            logger.warning("httpx is not installed, cannot fetch config from registry")
            return {"entities": []}
        except Exception as e:
            logger.warning(f"Failed to fetch config from registry: {e}")
            return {"entities": []}

    def merge_registry_configs(
        self,
        tools_data: Dict[str, Any],
        servers_data: Dict[str, Any],
        local_mcp_config: Dict[str, Any],
        use_tools_priority: bool = True
    ) -> Dict[str, Any]:
        """
        Merge registry data into MCP server configurations.
        This method processes raw registry data from both tools and servers sources and converts it to MCP configuration format.
        According to priority rules: tools > mcp_servers, only the higher priority data source will be processed.
        This method can be overridden by external implementations to use custom merge logic.
        
        Args:
            tools_data: Raw data from fetch_config_from_registry for tools, with structure: {
                "entities": [...]  # List of entities from registry
            }
            servers_data: Raw data from fetch_config_from_registry for servers, with structure: {
                "entities": [...]  # List of entities from registry
            }
            local_mcp_config: Local MCP configuration to filter out existing servers.
            use_tools_priority: If True, only process tools_data (tools priority is higher). If False, only process servers_data.
        
        Returns:
            Dict with structure: {
                "servers": ["env1", "env2"],  # Found server names (env1, env2, etc.)
                "configs": {                  # Server configurations grouped by version
                    "env1": {
                        "type": "streamable-http",
                        "url": "...",
                        "headers": {...},
                        "timeout": 6000,
                        ...
                    },
                    "env2": {...}
                }
            }
        """
        # According to priority: tools > mcp_servers, only process the higher priority data source
        if use_tools_priority:
            entities = tools_data.get("entities", [])
        else:
            entities = servers_data.get("entities", [])
        
        # Filter out entities that already exist in local_mcp_config
        local_server_names = set(local_mcp_config.get("mcpServers", {}).keys())
        filtered_entities = [
            entity for entity in entities
            if entity.get("name", "") not in local_server_names
        ]
        
        if not filtered_entities:
            return {"servers": [], "configs": {}}
        
        # Group entities by version
        version_groups = defaultdict(list)
        for entity in filtered_entities:
            version = entity.get("version", "default")
            version_groups[version].append(entity)
        
        # Build configs grouped by version (env1, env2, etc.)
        result_configs = {}
        result_servers = []
        env_counter = 1
        
        for version, version_entities in version_groups.items():
            env_name = f"env{env_counter}"
            env_counter += 1
            result_servers.append(env_name)
            
            # Collect all server names for this version
            version_server_names = []
            version_config = None
            
            for entity in version_entities:
                entity_name = entity.get("name", "")
                entity_data = entity.get("data", {})
                
                if not version_config:
                    # Initialize config from first entity
                    version_config = {
                        "type": entity_data.get("type", "streamable-http"),
                        "url": entity_data.get("url", ""),
                        "headers": copy.deepcopy(entity_data.get("headers", {})),
                        "timeout": entity_data.get("timeout", 6000),
                        "sse_read_timeout": entity_data.get("sse_read_timeout", 6000),
                        "client_session_timeout_seconds": entity_data.get("client_session_timeout_seconds", 6000)
                    }
                    # Set env_name in headers
                    if "headers" in version_config:
                        version_config["headers"]["env_name"] = env_name
                
                # Collect server name
                version_server_names.append(entity_name)
            
            # Merge MCP_SERVERS (comma-separated)
            if version_config and "headers" in version_config:
                mcp_servers_value = ",".join(version_server_names)
                version_config["headers"]["MCP_SERVERS"] = mcp_servers_value
            
            result_configs[env_name] = version_config
        
        return {
            "servers": result_servers,
            "configs": result_configs
        }

    async def _resolve_mcp_configuration(
        self,
        tools: List[str],
        mcp_servers: List[str],
        local_mcp_config: Dict[str, Any],
        local_mcp_servers: List[str],
        registry_url: str
    ) -> Tuple[List[str], Dict[str, Any]]:
        """
        Resolve final mcp_servers and mcp_config based on priority rules.
        
        Priority: tools > mcp_servers > mcp_config
        
        Args:
            tools: List of tools
            mcp_servers: List of MCP server names
            local_mcp_config: Backup of local MCP configuration
            local_mcp_servers: Local MCP servers extracted from mcp_config
            registry_url: Registry center URL
        
        Returns:
            Tuple of (final_mcp_servers, final_mcp_config)
        """
        # Initialize final config with local backup
        final_mcp_config = copy.deepcopy(local_mcp_config)
        final_mcp_servers = copy.deepcopy(local_mcp_servers)
        
        # Early return if registry_url is empty (no registry center to query)
        if not registry_url:
            return final_mcp_servers, final_mcp_config

        # Stage 1: Fetch raw data from registry for tools
        pending_tools_data = {"entities": []}
        if tools:
            try:
                registry_result = await self.fetch_config_from_registry(
                    registry_url, tools=tools
                )
                if registry_result:
                    pending_tools_data["entities"].extend(registry_result.get("entities", []))
            except Exception as e:
                logger.warning(f"Failed to fetch config from registry for tools: {e}")

        # Stage 2: Fetch raw data from registry for mcp_servers
        pending_servers_data = {"entities": []}
        if not tools:  # Only process if tools is empty (priority check)
            # Collect all servers that are not in local config
            servers_to_fetch = [
                server for server in (mcp_servers or [])
                if server not in final_mcp_config.get("mcpServers", {})
            ]
            if servers_to_fetch:
                try:
                    registry_result = await self.fetch_config_from_registry(
                        registry_url, servers=servers_to_fetch
                    )
                    if registry_result:
                        pending_servers_data["entities"].extend(registry_result.get("entities", []))
                except Exception as e:
                    logger.warning(f"Failed to fetch config from registry for servers {servers_to_fetch}: {e}")

        # Stage 3: Merge registry data into configurations
        # According to priority: tools > mcp_servers
        use_tools_priority = bool(tools)
        registry_result = self.merge_registry_configs(
            pending_tools_data, 
            pending_servers_data,
            local_mcp_config,
            use_tools_priority=use_tools_priority
        )
        registry_servers = registry_result.get("servers", [])
        registry_configs = registry_result.get("configs", {})

        # Stage 4: Merge configurations into final config
        if registry_configs:
            for server in registry_servers:
                if server not in final_mcp_config.get("mcpServers", {}):
                    final_mcp_servers.append(server)
                    if server in registry_configs:
                        final_mcp_config["mcpServers"][server] = registry_configs[server]

        return final_mcp_servers, final_mcp_config

    def _convert_custom_env_tools_to_mcp_config(
        self,
        custom_env_tools: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not custom_env_tools or not isinstance(custom_env_tools, dict):
            return None
        
        import os
        
        # Get environment variables
        url = os.getenv("CUSTOM_ENV_URL", "")
        token = os.getenv("CUSTOM_ENV_TOKEN", "")
        image_version = os.getenv("CUSTOM_ENV_IMAGE_VERSION", "")
        
        # Check if any environment variable is empty
        if not url or not token or not image_version:
            return None
        
        result_config = {
            "mcpServers": {}
        }
        
        # Convert each server in custom_env_tools
        for server_name, server_config in custom_env_tools.items():
            if not isinstance(server_config, dict):
                continue
            
            # Convert server_config to JSON string for MCP_CONFIG header (include key)
            server_config_str = json.dumps({server_name: server_config}, ensure_ascii=False)
            
            # Build streamable-http configuration
            streamable_config = {
                "type": "streamable-http",
                "url": url,
                "headers": {
                    "Authorization": f"Bearer {token}",
                    "IMAGE_VERSION": image_version,
                    "MCP_CONFIG": server_config_str
                },
                "timeout": 6000,
                "sse_read_timeout": 6000,
                "client_session_timeout_seconds": 6000
            }
            
            result_config["mcpServers"][server_name] = streamable_config
        
        return result_config if result_config["mcpServers"] else None

    async def remove(self) -> None:
        """Remove sandbox."""
        await self._remove_sandbox(
            sandbox_id=self.sandbox_id,
            metadata=self._metadata,
            env_type=self._env_type
        )

    async def cleanup(self) -> None:
        """Clean up Sandbox resources, including MCP server connections."""
        try:
            if hasattr(self, '_mcpservers') and self._mcpservers:
                await self._mcpservers.cleanup()
                logger.info(f"Cleaned up MCP servers for sandbox {self.sandbox_id}")
        except Exception as e:
            logger.warning(f"Failed to cleanup MCP servers: {e}")

        # Call the original remove method
        try:
            await self.remove()
        except Exception as e:
            logger.warning(f"Failed to remove sandbox: {e}")

    def get_skill_list(self) -> Optional[Any]:
        """Get the skill configurations.
        
        Returns:
            Optional[Any]: The skill configurations, or None if empty.
        """
        if self._skill_configs is None or not self._skill_configs:
            return None
        return self._skill_configs

    def __del__(self):
        super().__del__()
