import asyncio
from concurrent.futures import thread
import os
import threading
import traceback
import httpx
import logging
import websockets
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def novnc_proxy(request: Request):
    """
    Proxy for the VNC server.
    """

    async def proxy_pass_bytes(
        client: httpx.AsyncClient, method: str, url: str, headers: dict, content: bytes
    ) -> httpx.Response:
        if not headers:
            headers = {}
        headers["Authorization"] = f"Bearer {gen_auth_token()}"

        for header in [
            "host",
            "Host",
        ]:
            headers.pop(header, None)

        stream_response_context = client.stream(
            method=method,
            url=url,
            headers=headers,
            content=content,
        )

        stream_response = await stream_response_context.__aenter__()

        content_type = stream_response.headers.get("content-type", "")
        response_headers = dict(stream_response.headers)
        status_code = stream_response.status_code

        async def stream_bytes():
            try:
                async for chunk in stream_response.aiter_raw(chunk_size=1024):
                    yield chunk
            finally:
                await stream_response_context.__aexit__(None, None, None)

        return StreamingResponse(
            content=stream_bytes(),
            status_code=status_code,
            headers=response_headers,
            media_type=content_type,
        )

    try:
        return await proxy_pass_bytes(
            client=httpx.AsyncClient(),
            method=request.method,
            url=f"http://{novnc_server_addr()}{request.url.path}",
            headers=dict(request.headers),
            content=await request.body(),
        )
    except:
        logger.error(f"Novnc proxy pass error: {traceback.format_exc()}")
        raise


@router.websocket("/{token}/websockify")
async def websocket_novnc_proxy(websocket: WebSocket, token: str):
    """WebSocket proxy for noVNC websockify connections"""
    await websocket.accept()

    backend_ws_url = f"ws://{novnc_server_addr()}{websocket.url.path}"
    logger.info(f"WebSocket: Proxying to {backend_ws_url}")

    async def _proxy_websocket(client_ws, backend_ws):
        """Simple bidirectional WebSocket proxy"""

        async def client_to_backend():
            while True:
                try:
                    message = await client_ws.receive_bytes()
                except:
                    try:
                        message = await client_ws.receive_text()
                    except:
                        break
                await backend_ws.send(message)

        async def backend_to_client():
            async for message in backend_ws:
                if isinstance(message, bytes):
                    await client_ws.send_bytes(message)
                else:
                    await client_ws.send_text(message)

        await asyncio.gather(
            client_to_backend(), backend_to_client(), return_exceptions=True
        )

    try:
        headers = {"Authorization": f"Bearer {gen_auth_token()}"}
        for header in [
            "host",
            "Host",
        ]:
            headers.pop(header, None)

        async with websockets.connect(
            backend_ws_url, additional_headers=headers
        ) as backend_ws:
            await _proxy_websocket(websocket, backend_ws)
    except BaseException as e:
        logger.error(f"WebSocket proxy error: {traceback.format_exc()}")


_novnc_server_addr = None


def novnc_server_addr():
    global _novnc_server_addr
    return _novnc_server_addr


_auth_token = None


def gen_auth_token():
    global _auth_token
    return _auth_token


async def start_vnc_proxy(backend_addr: str, auth_token: str):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    import uvicorn

    global _novnc_server_addr
    _novnc_server_addr = backend_addr

    global _auth_token
    _auth_token = auth_token

    def start():
        uvicorn.run(app, host="0.0.0.0", port=8088)

    thread = threading.Thread(target=start, daemon=True)
    thread.start()
