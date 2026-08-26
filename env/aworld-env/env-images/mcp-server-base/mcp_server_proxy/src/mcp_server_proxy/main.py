from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import time
import traceback
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request, Response

from .mcp_server_proxy import MCPServerProxy

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


class PreRequestInitializationManager:
    """Manages initialization state and prevents race conditions."""

    def __init__(self, endpoint: str):
        self.lock = asyncio.Lock()
        self.init_status = 0  # 0: not initialized, 1: initialized, 10: initializing
        self.init_task = None
        self.endpoint = endpoint

    async def pre_request_init(self, request: Request):
        """Ensure initialization is complete before processing MCP requests."""

        if request.url.path != self.endpoint:
            return

        if self.init_status == 1:
            return

        async with self.lock:
            if self.init_status == 1:
                return
            elif self.init_status == 0:
                # Start initialization
                self.init_status = 10
                self.init_task = asyncio.create_task(self._initialize(request))
                logger.info("Starting initialization...")
            elif self.init_status == 10 and self.init_task is not None:
                # Wait for ongoing initialization
                logger.info("Waiting for initialization to complete...")
                try:
                    await self.init_task
                except BaseException as e:
                    logger.error(f"Initialization failed: {traceback.format_exc()}")
                    # Reset status to allow retry
                    self.init_status = 0
                    self.init_task = None
                    raise

    async def _initialize(self, request: Request):
        """Internal initialization method with proper error handling."""
        st = time.time()
        try:
            await _run_start_hook(request)
            self.init_status = 1
            self.init_task = None
            logger.info("Initialization completed successfully")
        except BaseException as e:
            logger.error(f"Initialization failed: {traceback.format_exc()}")
            raise
        finally:
            logger.info(f"Initialization cost: {(time.time() - st)*1000:.2f} ms")


# Global initialization manager instance
pre_request_init_manager = PreRequestInitializationManager(endpoint="/mcp")


async def mcp_pre_request_init(request: Request):
    """Pre-request initialization callback for MCP requests."""
    await pre_request_init_manager.pre_request_init(request)


async def _run_start_hook(request: Request):
    p = Path("/app/start.sh")
    if not p.exists():
        logger.info("MCP Server start hook not exists!")
        return
    image_env_str = request.headers.get("SANDBOX_ENV") or request.headers.get(
        "IMAGE_ENV"
    )
    image_env = {}
    if image_env_str:
        try:
            image_env = json.loads(image_env_str)
        except BaseException as e:
            logger.error(f"Error parsing image env: {traceback.format_exc()}")
    try:
        logger.info(f"Executing MCP Server start hook: {p.read_text()}")
        proc = await asyncio.subprocess.create_subprocess_shell(
            str(p),
            env=image_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        stdout_str = stdout.decode() if stdout else ""
        stderr_str = stderr.decode() if stderr else ""
        logger.info(
            f"MCP Server start hook output: exit_code={proc.returncode}, stdout={stdout_str}, stderr={stderr_str}"
        )
    except:
        logger.error(f"Error executing MCP Server start hook: {traceback.format_exc()}")


mcp = MCPServerProxy(
    name="MCP Server",
    stateless_http=False,
    host="0.0.0.0",
    port=8080,
    log_level="INFO",
    pre_request_callback=mcp_pre_request_init,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    # Lazy import FastAPI to improve startup speed
    from fastapi.responses import JSONResponse

    return JSONResponse(
        {
            "status": "success",
            "message": "MCP Server is healthy",
            "last_active": mcp.get_last_active_time(),
            "session_id": mcp.get_bind_session_id(),
        }
    )


async def main():
    logger.info("Starting MCP Server Proxy...")
    await mcp.initialize()
    await mcp.run_streamable_http_async()


if __name__ == "__main__":
    asyncio.run(main())
