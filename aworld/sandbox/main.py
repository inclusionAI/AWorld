import uuid
from enum import IntEnum
import asyncio

from typing_extensions import Optional, List, Any, Dict


from aworld.sandbox.base.api import SandboxApi
from aworld.sandbox.base.setup import SandboxSetup
from aworld.sandbox.models import SandboxStatus, SandboxEnvType, SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers


class Sandbox(SandboxSetup,SandboxApi):
    """
           Sandbox provides an isolated environment for executing code and operations.
    """

    @property
    def sandbox_id(self) -> str:
        """
        Unique identifier of the sandbox
        """
        return self._sandbox_id

    @property
    def status(self) -> SandboxStatus:
        """
        Sandbox status
        """
        return self._status

    @property
    def timeout(self) -> int:
        """
        Sandbox timeout
        """
        return self._timeout

    @property
    def metadata(self) -> Dict[str, str]:
        """
        Sandbox metadata
        """
        return self._metadata

    @property
    def env_type(self) -> SandboxEnvType:
        """
        Sandbox env type
        """
        return self._env_type


    @property
    def mcpservers(self) -> McpServers:
        """
        Module for running mcp in the sandbox.
        """
        return self._mcpservers

    @property
    def mcp_config(self) -> Any:
        """
        Sandbox env mcp_config
        """
        return self._mcp_config



    @property
    def mcp_servers(self) -> Any:
        """
         Sandbox env mcp_servers
        """
        return self._mcp_servers



    def __init__(
            self,
            sandbox_id: Optional[str] = None,
            env_type: Optional[int] = None,
            metadata: Optional[Dict[str, str]] = None,
            timeout: Optional[int] = None,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Optional[Any] = None,
    ):

        super().__init__()

        if sandbox_id:
            if not self._metadata:
                return self
            else:
                raise ValueError("sandbox_id is not exist")

        """
            Initialize sandbox status.
        """
        self._status = SandboxStatus.INIT
        """
            Initialize sandbox timeout.
        """
        self._timeout = timeout or self.default_sandbox_timeout
        """
            Initialize sandbox metadata.
        """
        self._metadata = metadata or {}
        """
            Initialize sandbox env type.
        """
        self._env_type = env_type or SandboxEnvType.LOCAL
        """
            Initialize sandbox_id with a default value
        """
        self._sandbox_id = sandbox_id

        """
            Initialize mcp_servers.
        """
        self._mcp_servers = mcp_servers

        """
            Initialize mcp_config.
        """
        self._mcp_config = mcp_config

        """
            Initialize a sandbox.
        """
        if not sandbox_id:
            response = SandboxApi._create_sandbox(
                env_type=self._env_type,
                env_config=None,
                mcp_servers=mcp_servers,
                mcp_config=mcp_config,
            )
            if not response:
                self._status = SandboxStatus.ERROR
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

        self._mcpservers = McpServers(
            mcp_servers,
            self._mcp_config,
        )

    def get_info(  # type: ignore
        self,
    ) -> SandboxInfo:

        return {
            "sandbox_id":self.sandbox_id,
            "status":self.status,
            "metadata":self.metadata,
            "env_type":self.env_type
        }

    async def remove(self) -> None:
        """
            Remove sandbox.
        """
        await SandboxApi._remove_sandbox(sandbox_id=self.sandbox_id,metadata=self._metadata,env_type=self._env_type)