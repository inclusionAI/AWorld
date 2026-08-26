import asyncio
import logging
import traceback
import websockets
from fastapi import APIRouter, WebSocket

from ..configs import channel_auth

from ..sessions.session_connection import SessionId

from ..auth import check_auth
from ..mcp_gateway import MCPGateway

logger = logging.getLogger(__name__)


router = APIRouter()


@router.websocket("/{mcp_session_id}/{full_path:path}")
async def websocket_stream_proxy(
    websocket: WebSocket, mcp_session_id: str, full_path: str
):
    """WebSocket proxy for stream connections"""
    await websocket.accept()

    # Auth check
    if channel_auth and not check_auth.check_ws_auth(websocket):
        logger.warning(
            f"Request unauthorized! request.url={websocket.url}, request.headers={websocket.headers}"
        )
        await websocket.close(code=4000, reason="Unauthorized")
        return

    # Get session connection
    gateway: MCPGateway = websocket.app.state.gateway
    session_connection = await gateway.session_connection_manager.get_avaliable_session(
        SessionId(session_id=mcp_session_id, mcp_session_id=mcp_session_id)
    )
    if not session_connection:
        logger.warning(
            f"WebSocket: Session invalid or expired: session_id={mcp_session_id}"
        )
        await websocket.close(code=4000, reason="Session invalid or expired")
        return

    # Proxy WebSocket to backend container
    backend_ws_url = f"ws://{session_connection.container_ip_addr}:{session_connection.channel_port}/{full_path}"
    logger.info(f"WebSocket: Proxying to {backend_ws_url}")

    async def _proxy_websocket(client_ws, backend_ws):
        """Simple bidirectional WebSocket proxy"""

        closed = False

        async def client_to_backend():
            nonlocal closed
            while not closed:
                try:
                    data = await client_ws.receive()
                    if data["type"] == "websocket.disconnect":
                        logger.info("Client WebSocket disconnected")
                        closed = True
                        try:
                            await client_ws.close()
                        except:
                            pass
                        try:
                            await backend_ws.close()
                        except:
                            pass
                        break

                    if "text" in data:
                        await backend_ws.send(data["text"])
                    elif "bytes" in data:
                        await backend_ws.send(data["bytes"])
                except:
                    logger.error(
                        f"WebSocket proxy client_to_backend error: {traceback.format_exc()}"
                    )
                    closed = True
                    try:
                        await client_ws.close()
                    except:
                        pass
                    try:
                        await backend_ws.close()
                    except:
                        pass
                    break

        async def backend_to_client():
            nonlocal closed
            try:
                async for message in backend_ws:
                    if isinstance(message, bytes):
                        await client_ws.send_bytes(message)
                    else:
                        await client_ws.send_text(message)
                logger.info("Backend WebSocket Connection closed")
            except:
                logger.error(
                    f"WebSocket proxy backend_to_client error: {traceback.format_exc()}"
                )
                closed = True
                try:
                    await client_ws.close()
                except:
                    pass
                try:
                    await backend_ws.close()
                except:
                    pass

        # Use asyncio.wait to ensure that if one task finishes, we can handle it
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(client_to_backend()),
                asyncio.create_task(backend_to_client()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel the remaining task
        for task in pending:
            task.cancel()

    try:
        async with websockets.connect(backend_ws_url) as backend_ws:
            await _proxy_websocket(websocket, backend_ws)
        logger.info(f"WebSocket proxy finished: {backend_ws_url}")
    except:
        logger.error(f"WebSocket proxy error: {traceback.format_exc()}")
