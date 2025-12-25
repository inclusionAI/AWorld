import asyncio
import copy
import json
import os
import uuid
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
from pathlib import Path

from aworld.logs.util import logger
from aworld.sandbox.api.local.sandbox_api import LocalSandboxApi
from aworld.sandbox.models import SandboxStatus, SandboxEnvType, SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers
from aworld.sandbox.common import BaseSandbox
from aworld.utils.common import sync_exec
from aworld.sandbox.utils.util import is_url, is_remote_url


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
            agents: Optional[Dict[str, Any]] = None,
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
            custom_env_tools=custom_env_tools,
            agents=agents
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
        self._tools = tools or []
        self._custom_env_tools = custom_env_tools
        self._agents = agents

        # Initialize sandbox if configuration is provided
        # Support lazy initialization: if no config provided, skip initialization
        if mcp_config or mcp_servers or tools or custom_env_tools or agents:
            self._initialize_sandbox(
                mcp_servers=mcp_servers,
                mcp_config=mcp_config,
                black_tool_actions=black_tool_actions,
                skill_configs=skill_configs,
                tools=tools,
                custom_env_tools=custom_env_tools,
                agents=agents
            )
        else:
            # Mark as not initialized for lazy initialization
            self._initialized = False

    def _initialize_sandbox(
        self,
        mcp_servers: Optional[List[str]] = None,
        mcp_config: Optional[Any] = None,
        black_tool_actions: Optional[Dict[str, List[str]]] = None,
        skill_configs: Optional[Any] = None,
        tools: Optional[List[str]] = None,
        custom_env_tools: Optional[Any] = None,
        agents: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize sandbox with MCP configuration.
        This method can be called during __init__ or later for lazy initialization.
        
        Args:
            mcp_servers: List of MCP servers to use.
            mcp_config: Configuration for MCP servers.
            black_tool_actions: Black list of tool actions.
            skill_configs: Skill configurations.
            tools: List of tools.
            custom_env_tools: Custom environment tools.
            agents: Custom environment agents. Supports mixed mode (string or dict).
        """
        # Use instance attributes if not provided
        mcp_servers = mcp_servers if mcp_servers is not None else self._mcp_servers
        mcp_config = mcp_config if mcp_config is not None else self._mcp_config
        black_tool_actions = black_tool_actions if black_tool_actions is not None else self._black_tool_actions
        skill_configs = skill_configs if skill_configs is not None else self._skill_configs
        tools = tools if tools is not None else self._tools
        custom_env_tools = custom_env_tools if custom_env_tools is not None else self._custom_env_tools
        agents = agents if agents is not None else self._agents

        # Update instance attributes
        self._mcp_servers = mcp_servers
        self._mcp_config = mcp_config
        self._black_tool_actions = black_tool_actions or {}
        self._skill_configs = skill_configs
        self._tools = tools or []
        self._custom_env_tools = custom_env_tools
        self._agents = agents

        # Keep original logic: if mcp_config exists and mcp_servers is empty, populate from mcp_config
        if mcp_config and not mcp_servers:
            mcp_servers = list(mcp_config.get("mcpServers", {}).keys())
            self._mcp_servers = mcp_servers

        # Resolve MCP configuration based on priority: tools > mcp_servers > mcp_config
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

        # Step 5: Convert agents to MCP config and merge
        if agents:
            agents_mcp_config = self._convert_agents_to_mcp_config(agents)
            if agents_mcp_config:
                # Merge agents config into final_mcp_config
                for server_name, server_config in agents_mcp_config.get("mcpServers", {}).items():
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

        # Initialize or reinitialize McpServers with a reference to this sandbox instance
        # Clean up existing instance if reinitializing
        if hasattr(self, '_mcpservers') and self._mcpservers:
            try:
                # Use sync_exec to handle async cleanup
                sync_exec(self._mcpservers.cleanup)
            except Exception as e:
                logger.warning(f"Failed to cleanup existing MCP servers: {e}")

        self._mcpservers = McpServers(
            mcp_servers=final_mcp_servers,
            mcp_config=final_mcp_config,
            sandbox=self,
            black_tool_actions=self._black_tool_actions,
            skill_configs=self._skill_configs,
            tool_actions=tools
        )
        
        # Mark as initialized
        self._initialized = True

    def _reinitialize_mcpservers(self):
        """
        Reinitialize MCP servers when configuration changes.
        This is called automatically when mcp_config, mcp_servers, black_tool_actions, or skill_configs are set.
        """
        if not self._initialized:
            # If not initialized yet, do full initialization
            self._initialize_sandbox()
        else:
            # Reinitialize only MCP servers part
            final_mcp_servers = self._mcp_servers
            final_mcp_config = copy.deepcopy(self._mcp_config) if self._mcp_config else {}
            
            # Handle custom_env_tools: convert and merge into mcp_config
            if self._custom_env_tools:
                custom_mcp_config = self._convert_custom_env_tools_to_mcp_config(self._custom_env_tools)
                if custom_mcp_config:
                    # Merge custom_env_tools config into final_mcp_config
                    if "mcpServers" not in final_mcp_config:
                        final_mcp_config["mcpServers"] = {}
                    for server_name, server_config in custom_mcp_config.get("mcpServers", {}).items():
                        if server_name not in final_mcp_config.get("mcpServers", {}):
                            final_mcp_servers.append(server_name)
                        final_mcp_config["mcpServers"][server_name] = server_config
            
            # Handle agents: convert and merge into mcp_config
            if self._agents:
                agents_mcp_config = self._convert_agents_to_mcp_config(self._agents)
                if agents_mcp_config:
                    # Merge agents config into final_mcp_config
                    if "mcpServers" not in final_mcp_config:
                        final_mcp_config["mcpServers"] = {}
                    for server_name, server_config in agents_mcp_config.get("mcpServers", {}).items():
                        if server_name not in final_mcp_config.get("mcpServers", {}):
                            final_mcp_servers.append(server_name)
                        final_mcp_config["mcpServers"][server_name] = server_config
            
            # Clean up existing instance (async cleanup in sync context)
            if hasattr(self, '_mcpservers') and self._mcpservers:
                try:
                    # Use sync_exec to handle async cleanup
                    sync_exec(self._mcpservers.cleanup)
                except Exception as e:
                    logger.warning(f"Failed to cleanup existing MCP servers: {e}")

            # Recreate McpServers instance
            self._mcpservers = McpServers(
                mcp_servers=final_mcp_servers,
                mcp_config=final_mcp_config,
                sandbox=self,
                black_tool_actions=self._black_tool_actions,
                skill_configs=self._skill_configs,
                tool_actions=self._tools
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
            registry_url: Registry center URL or local file path
            tools: List of tool names to find corresponding servers (when tools is provided)
            servers: List of server names to fetch configurations (when servers is provided)
        
        Returns:
            Dict with structure: {
                "entities": [...]  # Raw entities from registry response
            }
        """
        if not registry_url:
            return {"entities": []}

        # Check if registry_url is a local file path
        if not is_url(registry_url):
            return self._fetch_from_local_file(registry_url, tools=tools, servers=servers)

        # Remote registry
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

    def _fetch_from_local_file(
        self,
        file_path: str,
        tools: Optional[List[str]] = None,
        servers: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Fetch entities from local JSON file.
        
        The local registry file uses dict format with entity_type:name as key:
        {
            "tool:server_name": {
                "entity_type": "tool",
                "name": "server_name",
                "description": "...",
                "tools": [...],
                "data": {...}
            },
            ...
        }
        
        Args:
            file_path: Path to local registry file
            tools: List of tool names to filter
            servers: List of server names to filter
        
        Returns:
            Dict with structure: {"entities": [...]}
        """
        try:
            # Expand user path (~/workspace -> /Users/username/workspace)
            path = Path(file_path).expanduser()
            
            # If file doesn't exist, return empty entities
            if not path.exists():
                return {"entities": []}
            
            # Read and parse JSON file
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate that data is a dict
            if not isinstance(data, dict):
                logger.warning(f"Registry file {file_path} is not a valid dict format")
                return {"entities": []}
            
            # Convert dict format to entities list
            entities = []
            for entity_key, entity in data.items():
                # Skip if entity is not a dict
                if not isinstance(entity, dict):
                    continue
                
                # Extract entity_type and name from key (format: "entity_type:name")
                if ":" in entity_key:
                    entity_type, name = entity_key.split(":", 1)
                    # Ensure entity has correct type and name
                    if entity.get("entity_type") == entity_type and entity.get("name") == name:
                        entities.append(entity)
                else:
                    # Fallback: use entity as is if key format is unexpected
                    entities.append(entity)
            
            # Filter by servers if provided
            if servers:
                entities = [e for e in entities if e.get("name") in servers]
            
            # Filter by tools if provided
            if tools:
                filtered_entities = []
                for entity in entities:
                    entity_tools = entity.get("tools", [])
                    # Check if any tool name matches
                    tool_names = [t.get("name", "") for t in entity_tools if isinstance(t, dict)]
                    # Match tool names
                    matches = False
                    for tool in tools:
                        if tool in tool_names:
                            matches = True
                            break
                    if matches:
                        filtered_entities.append(entity)
                entities = filtered_entities
            
            return {"entities": entities}
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse registry file {file_path}: {e}")
            return {"entities": []}
        except Exception as e:
            logger.warning(f"Failed to fetch from local file {file_path}: {e}")
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

        # Stage 3: Process registry data based on registry type (remote vs local)
        # Check if registry_url is a local file path
        is_local_registry = not is_url(registry_url)
        
        if is_local_registry:
            # For local registry: directly use entities as server configs
            # According to priority: tools > mcp_servers
            use_tools_priority = bool(tools)
            entities = pending_tools_data.get("entities", []) if use_tools_priority else pending_servers_data.get("entities", [])
            
            # Filter out entities that already exist in local_mcp_config
            local_server_names = set(local_mcp_config.get("mcpServers", {}).keys())
            filtered_entities = [
                entity for entity in entities
                if entity.get("name", "") not in local_server_names
            ]
            
            # Directly convert entities to configs (no version grouping for local registry)
            registry_servers = []
            registry_configs = {}
            for entity in filtered_entities:
                entity_name = entity.get("name", "")
                entity_data = entity.get("data", {})
                
                if entity_name and entity_data:
                    registry_servers.append(entity_name)
                    registry_configs[entity_name] = entity_data
        else:
            # For remote registry: use merge_registry_configs (with version grouping)
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
            # Format: {"mcpServers": {server_name: server_config}}
            server_config_str = json.dumps({"mcpServers": {server_name: server_config}}, ensure_ascii=False)
            
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

    def _convert_remote_agent_to_mcp_config(
        self,
        agent_name: str,
        location: str,
        extra_config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Convert a remote agent to MCP streamable-http configuration.
        
        This method follows the same logic as _convert_custom_env_tools_to_mcp_config:
        - Uses environment variables (CUSTOM_ENV_URL, CUSTOM_ENV_TOKEN, CUSTOM_ENV_IMAGE_VERSION)
        - Builds server_config with the location (URL) and extra_config
        - Converts to streamable-http MCP server config
        
        Args:
            agent_name: Name of the agent
            location: Remote URL (e.g., "https://github.com/..." or "git@github.com:...")
            extra_config: Additional configuration (env, args, etc.)
        
        Returns:
            Dict with structure: {
                "type": "streamable-http",
                "url": "...",
                "headers": {...},
                "timeout": 6000,
                ...
            }
            Returns None if environment variables are not set.
        """
        import os
        
        # Get environment variables (same as _convert_custom_env_tools_to_mcp_config)
        url = os.getenv("CUSTOM_ENV_URL", "")
        token = os.getenv("CUSTOM_ENV_TOKEN", "")
        image_version = os.getenv("CUSTOM_ENV_IMAGE_VERSION", "")
        
        # Check if any environment variable is empty
        if not url or not token or not image_version:
            logger.warning(f"Remote agent '{agent_name}' requires CUSTOM_ENV_URL, CUSTOM_ENV_TOKEN, and CUSTOM_ENV_IMAGE_VERSION environment variables")
            return None
        
        # Extract headers from extra_config if present (for streamable-http headers)
        # Headers should be merged into streamable_config headers, not server_config
        extra_headers = extra_config.pop("headers", {}) if isinstance(extra_config.get("headers"), dict) else {}
        
        # Build server_config with location and extra_config (excluding headers)
        # The location (URL) will be passed as repo_url in the server_config
        # If extra_config already has repo_url, it will be used; otherwise use location
        server_config = {
            "type": "remote",
            **extra_config  # Merge any additional config (env, args, project_path, etc., but not headers)
        }
        # Set repo_url: use location if not already set in extra_config
        if "repo_url" not in server_config:
            server_config["repo_url"] = location
        
        # Convert server_config to JSON string for MCP_CONFIG header (include key)
        # Format: {"mcpServers": {agent_name: server_config}}
        server_config_str = json.dumps({"mcpServers": {agent_name: server_config}}, ensure_ascii=False)
        
        # Build streamable-http configuration (same as _convert_custom_env_tools_to_mcp_config)
        # Merge extra_headers into default headers (extra_headers take precedence)
        streamable_config = {
            "type": "streamable-http",
            "url": url,
            "headers": {
                "Authorization": f"Bearer {token}",
                "IMAGE_VERSION": image_version,
                "MCP_CONFIG": server_config_str,
                **extra_headers  # Merge extra headers (user-provided headers take precedence)
            },
            "timeout": 6000,
            "sse_read_timeout": 6000,
            "client_session_timeout_seconds": 6000
        }
        
        return streamable_config


    def _convert_local_agent_to_mcp_config(
        self,
        agent_name: str,
        location: str,
        extra_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Convert a local agent to MCP stdio configuration using aworld-cli.
        
        This method generates a stdio MCP configuration that executes aworld-cli
        to start an MCP stdio server. The location should be an agents directory path.
        
        Args:
            agent_name: Name of the agent
            location: Agents directory path (e.g., "/path/to/agents")
            extra_config: Additional configuration (env, cwd, etc.)
        
        Returns:
            Dict with structure: {
                "type": "stdio",
                "command": "aworld-cli",  # aworld-cli command
                "args": ["serve", "--mcp", "--agent-dir", "/path/to/agents"],  # command arguments
                "env": {...},  # from extra_config if provided, with auto-set AGENTS_DIR and parsed SANDBOX_ENV
                "cwd": "...",  # from extra_config if provided, or agents_dir parent
                "encoding": "utf-8",  # from extra_config if provided
                "encoding_error_handler": "strict",  # from extra_config if provided
                "client_session_timeout_seconds": 120,  # from extra_config if provided
            }
        """
        from pathlib import Path
        
        # Resolve location to absolute path (agents directory)
        agents_dir_path = Path(location).resolve()
        if not agents_dir_path.exists():
            logger.warning(f"Agent '{agent_name}' agents directory does not exist: {agents_dir_path}")
        
        # Use aworld-cli command
        command = "aworld-cli"
        
        # Build args: serve --mcp --agent-dir <agents_dir>
        agents_dir_str = str(agents_dir_path)
        args = [
            "serve",
            "--mcp",
            "--agent-dir",
            agents_dir_str
        ]
        
        # Build stdio configuration
        stdio_config = {
            "type": "stdio",
            "command": command,
            "args": args
        }
        
        # Handle environment variables
        # Merge with existing env if provided, or create new dict
        env = extra_config.get("env", {})
        if not isinstance(env, dict):
            env = {}
        
        # Auto-set AGENTS_DIR environment variable
        # This is used by aworld-cli as fallback if not provided as argument
        if "AGENTS_DIR" not in env:
            env["AGENTS_DIR"] = agents_dir_str
        
        # Handle headers: extract SANDBOX_ENV from headers and merge into env
        # This allows passing sandbox environment variables through headers
        headers = extra_config.get("headers", {})
        if isinstance(headers, dict) and "SANDBOX_ENV" in headers:
            try:
                sandbox_env_str = headers.get("SANDBOX_ENV")
                if isinstance(sandbox_env_str, str):
                    # Parse JSON string and merge into env
                    sandbox_env = json.loads(sandbox_env_str)
                    if isinstance(sandbox_env, dict):
                        # Merge sandbox_env into env (env takes precedence if keys conflict)
                        env = {**sandbox_env, **env}
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse SANDBOX_ENV from headers for agent '{agent_name}': {e}")
        
        # Set env if we have any environment variables
        if env:
            stdio_config["env"] = env
        
        # Handle working directory (cwd)
        # If not specified, default to agents directory's parent for better path resolution
        if "cwd" in extra_config:
            stdio_config["cwd"] = extra_config.get("cwd")
        else:
            # Auto-set cwd to agents directory's parent
            stdio_config["cwd"] = str(agents_dir_path.parent.resolve())
        
        # Add optional fields from extra_config
        if "encoding" in extra_config:
            stdio_config["encoding"] = extra_config.get("encoding")
        
        if "encoding_error_handler" in extra_config:
            stdio_config["encoding_error_handler"] = extra_config.get("encoding_error_handler")
        
        if "client_session_timeout_seconds" in extra_config:
            stdio_config["client_session_timeout_seconds"] = extra_config.get("client_session_timeout_seconds")
        
        return stdio_config

    def _convert_agents_to_mcp_config(
        self,
        agents: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Convert agents to MCP configuration.
        
        This method supports two formats (mixed mode):
        1. Simple format: Direct string value (auto-detected as local path or remote URL)
        2. Extended format: Dictionary with 'location' field and optional additional config
        
        Args:
            agents: Dictionary mapping agent names to their configurations.
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
        
        Returns:
            Dict with structure: {
                "mcpServers": {
                    "agent_name": {
                        "type": "stdio" | "streamable-http" | ...,
                        "command": "...",  # for local type
                        "args": [...],     # for local type
                        "url": "...",      # for remote type
                        ...
                    },
                    ...
                }
            }
            Returns None if agents is empty or invalid.
        """
        if not agents or not isinstance(agents, dict):
            return None
        
        result_config = {
            "mcpServers": {}
        }
        
        # Process each agent in agents
        for agent_name, agent_config in agents.items():
            location = None
            is_remote = False
            extra_config = {}
            
            # Handle simple format: string value
            if isinstance(agent_config, str):
                location = agent_config
                is_remote = is_remote_url(location)
            
            # Handle extended format: dictionary
            elif isinstance(agent_config, dict):
                # Get location from 'location' field
                location = agent_config.get("location")
                if not location:
                    logger.warning(f"Agent '{agent_name}' missing 'location' field in config")
                    continue
                
                if not isinstance(location, str):
                    logger.warning(f"Agent '{agent_name}' has invalid 'location' field (expected string, got {type(location)})")
                    continue
                
                # Check if type is explicitly provided
                if "type" in agent_config:
                    # Use explicit type field (case-insensitive)
                    agent_type = agent_config.get("type")
                    if isinstance(agent_type, str):
                        agent_type = agent_type.lower().strip()
                        if agent_type == "remote":
                            is_remote = True
                        elif agent_type == "local":
                            is_remote = False
                        else:
                            logger.warning(f"Agent '{agent_name}' has invalid 'type' field ('{agent_type}'), expected 'local' or 'remote'. Defaulting to 'local'.")
                            is_remote = False
                    else:
                        logger.warning(f"Agent '{agent_name}' has invalid 'type' field (expected string, got {type(agent_type)}). Defaulting to 'local'.")
                        is_remote = False
                else:
                    # Type not provided, auto-detect based on location
                    is_remote = is_remote_url(location)
                
                # Extract extra config (excluding 'location' and 'type')
                extra_config = {k: v for k, v in agent_config.items() if k not in ("location", "type")}
            
            else:
                logger.warning(f"Invalid agent config for '{agent_name}': expected string or dict, got {type(agent_config)}")
                continue
            
            if not location:
                logger.warning(f"Agent '{agent_name}' has empty location")
                continue
            
            # Process based on detected type
            if is_remote:
                # Handle remote agent
                mcp_config = self._convert_remote_agent_to_mcp_config(agent_name, location, extra_config)
                if mcp_config:
                    result_config["mcpServers"][agent_name] = mcp_config
                else:
                    logger.warning(f"Failed to convert remote agent '{agent_name}' to MCP config")
            else:
                # Handle local agent
                mcp_config = self._convert_local_agent_to_mcp_config(agent_name, location, extra_config)
                result_config["mcpServers"][agent_name] = mcp_config
        
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
