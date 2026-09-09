"""
Streamable HTTP MCP Service Proxy

A gateway service that forwards MCP requests to bound backend MCP services
with persistent HTTP connections. Implements the streamable HTTP protocol
for Model Context Protocol (MCP) communication.
"""

import logging
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException

from .utils.common_utils import get_remote_addr
from .utils.log_context import TraceIdFilter, set_trace_id, reset_trace_id
from .auth import check_auth
from .mcp_gateway import MCPGateway

from .routers import (
    gateway_api,
    novnc_proxy_pass,
    stream_proxy_pass,
    http_proxy_pass,
    dashboard,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - [trace_id=%(trace_id)s]: %(message)s",
)
logger = logging.getLogger(__name__)
trace_id_filter = TraceIdFilter()
root_logger = logging.getLogger()
for handler in root_logger.handlers:
    handler.addFilter(trace_id_filter)

# Suppress httpx INFO logs
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup and shutdown events"""
    # Initialize gateway
    gateway = MCPGateway()
    await gateway.startup()
    app.state.gateway = gateway
    try:
        yield
    finally:
        # Shutdown
        await gateway.shutdown()


# FastAPI application setup with lifespan
app = FastAPI(
    title="MCP",
    description="MCP Service",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


@app.api_route("/mcp", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def mcp_proxy(request: Request):
    """
    Main MCP proxy endpoint.
    Accepts all HTTP methods and forwards them to backend MCP servers.
    """
    trace_id = request.headers.get("TRACE_ID", "NA")
    token = set_trace_id(trace_id)
    try:
        if not check_auth.check_mcp_auth(request):
            logger.warning(
                f"MCP Request unauthorized! remote.addr={get_remote_addr(request)}, request.headers={request.headers}"
            )
            return Response(status_code=401, content="Unauthorized")

        try:
            gateway: MCPGateway = request.app.state.gateway
            return await gateway.handle_mcp_request(request)
        except BaseException as e:
            logger.error(
                f"Request error: remote.addr={get_remote_addr(request)}, request.headers={request.headers}\n{traceback.format_exc()}"
            )
            raise HTTPException(status_code=500, detail=f"Internal server error, {e}")
    finally:
        reset_trace_id(token)


# NoVNC proxy pass
app.include_router(novnc_proxy_pass.router, prefix="/novnc")

# Stream proxy pass
app.include_router(stream_proxy_pass.router, prefix="/stream")

# HTTP proxy pass
app.include_router(http_proxy_pass.router, prefix="/http")

# Gateway rest api
app.include_router(gateway_api.router, prefix="/api")

# Gateway dashboard
app.include_router(dashboard.router, prefix="/dashboard")


@app.get("/health")
async def health(request: Request):
    return {"status": "success", "message": "MCP Gateway is healthy"}


if __name__ == "__main__":
    import uvicorn

    # Run the server
    uvicorn.run("mcp_gateway.main:app", host="0.0.0.0", port=8000, workers=4)
