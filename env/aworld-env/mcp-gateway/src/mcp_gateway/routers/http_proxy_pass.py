import asyncio
import logging
import traceback
import websockets
from fastapi import APIRouter, Request, HTTPException, Response, WebSocket

from ..configs import vnc_auth

from ..sessions.session_connection import SessionId

from ..auth import check_auth
from ..utils.common_utils import get_remote_addr
from ..mcp_gateway import MCPGateway

logger = logging.getLogger(__name__)

router = APIRouter()


@router.api_route(
    "/{mcp_session_id}/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def http_proxy(request: Request, mcp_session_id: str):
    """
    Proxy for the VNC server.
    """
    # Auth check
    if vnc_auth and not check_auth.check_ws_auth(request):
        logger.warning(
            f"VNC Request unauthorized! remote.addr={get_remote_addr(request)}, request.headers={request.headers}"
        )
        return Response(status_code=401, content="Unauthorized")

    gateway: MCPGateway = request.app.state.gateway
    session_connection = await gateway.session_connection_manager.get_avaliable_session(
        SessionId(session_id=mcp_session_id, mcp_session_id=mcp_session_id)
    )

    if not session_connection:
        logger.warning(
            f"Session is invalid or expired: remote.addr={get_remote_addr(request)}, request.headers={request.headers}, session_id={mcp_session_id}"
        )
        raise HTTPException(status_code=400, detail="Session is invalid or expired")
    return await session_connection.http_proxy(request)
