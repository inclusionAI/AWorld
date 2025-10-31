import asyncio
import uuid
from typing import Dict, List, Any, Optional

from aworld.logs.util import logger
from aworld.sandbox.common import BaseSandbox
from aworld.sandbox.api.kubernetes.sandbox_api import KubernetesSandboxApi
from aworld.sandbox.models import SandboxStatus, SandboxEnvType, SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers


class KubernetesSandbox(BaseSandbox, KubernetesSandboxApi):
    """
    Kubernetes sandbox implementation that runs in a Kubernetes cluster.
    """

    def __init__(
            self,
            sandbox_id: Optional[str] = None,
            metadata: Optional[Dict[str, str]] = None,
            timeout: Optional[int] = None,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Optional[Any] = None,
            **kwargs
    ):
        """Initialize a new KubernetesSandbox instance.
        
        Args:
            sandbox_id: Unique identifier for the sandbox. If None, one will be generated.
            metadata: Additional metadata for the sandbox.
            timeout: Timeout for sandbox operations.
            mcp_servers: List of MCP servers to use.
            mcp_config: Configuration for MCP servers.
            **kwargs: Additional parameters for specific sandbox types.
        """
        super().__init__(
            sandbox_id=sandbox_id,
            env_type=SandboxEnvType.K8S,
            metadata=metadata,
            timeout=timeout,
            mcp_servers=mcp_servers,
            mcp_config=mcp_config,
            black_tool_actions=kwargs.get('black_tool_actions'),
            skill_configs=kwargs.get('skill_configs')
        )

        if sandbox_id:
            if not self._metadata:
                return self
            else:
                raise ValueError("sandbox_id is not exist")

        # Initialize properties
        self._status = SandboxStatus.INIT
        self._timeout = timeout or self.default_sandbox_timeout
        self._metadata = metadata or {}
        self._env_type = SandboxEnvType.K8S
        self._mcp_servers = mcp_servers
        self._mcp_config = mcp_config
        self._skill_configs = kwargs.get('skill_configs', {})
        self._black_tool_actions = kwargs.get('black_tool_actions', {})

        # Ensure sandbox_id has a value in all cases
        self._sandbox_id = sandbox_id or str(uuid.uuid4())

        # If no sandbox_id provided, create a new sandbox
        if not sandbox_id:
            response = self._create_sandbox(
                env_type=self._env_type,
                env_config=None,
                mcp_servers=mcp_servers,
                mcp_config=mcp_config,
                black_tool_actions=self._black_tool_actions,
                skill_configs=self._skill_configs,
            )

            if not response:
                self._status = SandboxStatus.ERROR
                # If creation fails, keep the generated UUID as the ID
                logger.warning(f"Failed to create K8s sandbox, using generated ID: {self._sandbox_id}")
            else:
                self._sandbox_id = response.sandbox_id
                self._status = SandboxStatus.RUNNING
                self._metadata = {
                    "pod_name": getattr(response, 'pod_name', None),
                    "service_name": getattr(response, 'service_name', None),
                    "status": getattr(response, 'status', None),
                    "cluster_ip": getattr(response, 'cluster_ip', None),
                    "host": getattr(response, 'host', None),
                    "mcp_config": getattr(response, 'mcp_config', None),
                    "env_type": getattr(response, 'env_type', None),
                }
                self._mcp_config = getattr(response, 'mcp_config', None)
                self._skill_configs = getattr(response, 'skill_configs', None)

        # Initialize McpServers
        self._mcpservers = McpServers(
            mcp_servers,
            self._mcp_config,
            sandbox=self,
            black_tool_actions=self._black_tool_actions,
            skill_configs=self._skill_configs
        )

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
