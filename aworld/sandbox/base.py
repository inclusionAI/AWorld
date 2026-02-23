import abc
import asyncio
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

from aworld.logs.util import logger
from aworld.sandbox.api.setup import SandboxSetup
from aworld.sandbox.models import SandboxStatus, SandboxEnvType, SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers
from aworld.sandbox.runtime import SandboxManager


class BaseSandbox(SandboxSetup):
    """
    Abstract base class for sandbox implementations.
    Defines minimal attributes and interface; concrete behavior is in Sandbox (implementations/sandbox.py).
    """

    default_sandbox_timeout = 3000

    @property
    def sandbox_id(self) -> str:
        """Unique identifier of the sandbox."""
        return self._sandbox_id

    @property
    def status(self) -> SandboxStatus:
        """Current status of the sandbox."""
        return self._status

    @property
    def timeout(self) -> int:
        """Timeout value for sandbox operations."""
        return self._timeout

    @property
    def metadata(self) -> Dict[str, Any]:
        """Sandbox metadata."""
        return self._metadata

    @property
    def env_type(self) -> SandboxEnvType:
        """Environment type of the sandbox."""
        return self._env_type

    @property
    @abc.abstractmethod
    def mcpservers(self) -> McpServers:
        """MCP servers instance. Implemented by Sandbox."""
        pass

    def __init__(
        self,
        sandbox_id: Optional[str] = None,
        env_type: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ):
        """
        Initialize minimal sandbox state. Only base attributes are set here.
        Subclasses (e.g. Sandbox) set MCP/workspace/agents etc. in their own __init__.
        """
        self._sandbox_id = sandbox_id or str(uuid.uuid4())
        self._status = SandboxStatus.INIT
        self._timeout = timeout or self.default_sandbox_timeout
        self._metadata = metadata or {}
        self._env_type = env_type or SandboxEnvType.LOCAL
        # create = Sandbox object constructed; bound (in manager) = first time this sandbox_id gets a worker/loop
        logger.info(
            f"[sandbox create] sandbox_id={self._sandbox_id} pid={os.getpid()} tid={threading.get_ident()} at={datetime.now().isoformat(timespec='milliseconds')}"
        )
        if self._sandbox_id:
            SandboxManager.get_instance().register_sandbox(self._sandbox_id, self)

    @abc.abstractmethod
    def get_info(self) -> SandboxInfo:
        """Returns information about the sandbox."""
        pass

    @abc.abstractmethod
    async def remove(self) -> bool:
        """Remove the sandbox and clean up all resources."""
        pass

    @abc.abstractmethod
    def get_skill_list(self) -> Optional[Any]:
        """Get the skill configurations."""
        pass

    @abc.abstractmethod
    async def cleanup(self) -> bool:
        """Clean up the sandbox resources."""
        pass

    async def list_tools(self, context: Any = None) -> List[Dict[str, Any]]:
        """List all available tools from MCP servers. Delegates to mcpservers.list_tools()."""
        if hasattr(self, "mcpservers") and self.mcpservers is not None:
            return await self.mcpservers.list_tools(context=context)
        return []

    async def call_tool(
        self,
        action_list: List[Dict[str, Any]] = None,
        task_id: str = None,
        session_id: str = None,
        context: Any = None,
    ) -> List[Any]:
        """Call a tool on MCP servers. Delegates to mcpservers.call_tool()."""
        if hasattr(self, "mcpservers") and self.mcpservers is not None:
            return await self.mcpservers.call_tool(
                action_list=action_list,
                task_id=task_id,
                session_id=session_id,
                context=context,
            )
        return []

    def __del__(self):
        """Ensure resources are cleaned up when the object is garbage collected."""
        try:
            try:
                asyncio.get_running_loop()
                logging.warning("Cannot clean up sandbox in __del__ when event loop is already running")
                return
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.cleanup())
                loop.close()
        except Exception as e:
            logging.debug(f"Failed to cleanup sandbox resources during garbage collection: {e}")
