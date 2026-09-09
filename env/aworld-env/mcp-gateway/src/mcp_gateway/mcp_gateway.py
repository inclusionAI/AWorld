from abc import ABC
import logging
from fastapi import Request, Response

from .sessions import SessionId
from .utils.common_utils import (
    get_mcp_operation,
    get_remote_addr,
)
from .sessions import (
    SessionConnectionManager,
    session_connection_manager_builder,
)
from .containers import (
    ContainerServerManager,
    container_server_manager_builder,
)

logger = logging.getLogger(__name__)


class MCPGateway(ABC):
    """
    MCP Gateway service that proxies requests to backend MCP servers
    with persistent connections based on client connection caching.
    """

    def __init__(self):
        self.session_connection_manager: SessionConnectionManager
        self.container_server_manager: ContainerServerManager

    async def startup(self):
        """Initialize the gateway service"""
        logger.info("Starting MCP Gateway service")

        self.container_server_manager = await container_server_manager_builder()
        self.session_connection_manager = await session_connection_manager_builder(
            self.container_server_manager
        )

    async def shutdown(self):
        """Cleanup resources"""
        logger.info("Shutting down MCP Gateway service")
        await self.session_connection_manager.shutdown()
        await self.container_server_manager.shutdown()

    async def handle_mcp_request(self, request: Request) -> Response:
        """
        Handle incoming MCP requests and forward them to appropriate backend servers.
        Uses connection-based caching for persistent connections.
        """

        remote_addr = get_remote_addr(request)

        session_id = SessionId.from_request(request)

        http_method, mcp_client_method, mcp_tool_method, mcp_tool_params = (
            await get_mcp_operation(request)
        )

        # Handle session initialize request
        if (
            http_method == "POST"
            and not session_id.mcp_session_id
            and mcp_client_method == "initialize"
        ):
            return await self.session_connection_manager.handle_initialize_request(
                request
            )

        # Handle seesion finalize request
        elif http_method == "DELETE" and session_id.mcp_session_id:
            return await self.session_connection_manager.handle_delete_request(
                request, session_id
            )

        else:
            # Handle other requests
            assert session_id.mcp_session_id, "Mcp-Session-Id is required"

            async def on_complete(response_body: str, e: Exception | None):
                logger.info(
                    f"MCP client request {'error' if e else 'success'}: request.addr={remote_addr}, session_id={session_id}, mcp_operation={http_method, mcp_client_method, mcp_tool_method}, response_body={response_body[:500]}, {'error=' + str(e) if e else ''}"
                )

            return await self.session_connection_manager.forward_request(
                request, session_id, on_complete
            )
