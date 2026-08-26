from contextlib import asynccontextmanager
import asyncio
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging

from .configs import container_server_port, docker_health_timeout_sec
from .dockers import docker_helper
from . import container_server_manager
from .utils.log_context import TraceIdFilter, set_trace_id, reset_trace_id

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - [trace_id=%(trace_id)s]: %(message)s",
)
trace_id_filter = TraceIdFilter()
root_logger = logging.getLogger()
for handler in root_logger.handlers:
    handler.addFilter(trace_id_filter)

# Suppress httpx INFO logs
logging.getLogger("httpx").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup and shutdown events"""
    # Startup
    await container_server_manager.wait_oss_ready()
    await container_server_manager.wait_docker_ready()
    await container_server_manager.ensure_docker_env()
    await container_server_manager.load_mcp_server_image()
    await container_server_manager.start_mcp_server_life_cycle_manager()
    await container_server_manager.start_container_server_register_task()
    try:
        yield
    finally:
        # Shutdown
        # await mcp_server_manager.clean_mcp_server_container()
        pass


# FastAPI application setup with lifespan
app = FastAPI(
    title="MCP Container Server",
    description="MCP Container Server",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/api/container/create")
async def create_container(request: Request, body: dict):
    token = set_trace_id(request.headers.get("TRACE_ID", "NA"))
    try:
        logger.info(f"Create container: body={body}")
        _ = body.get("token")

        session_id = body.get("session_id")
        image_version = body.get("image_version")
        image_env = body.get("image_env")

        container_id, ip_addr, mcp_port, novnc_port, channel_port, http_port = (
            await container_server_manager.create_mcp_server_container(
                session_id, image_version, image_env
            )
        )
        logger.info(
            f"Container created: container_id={container_id}, ip_addr={ip_addr}, mcp_port={mcp_port}, novnc_port={novnc_port}"
        )

        return {
            "status": "success",
            "message": f"MCP server created: {ip_addr}:{mcp_port}",
            "data": {
                "ip_addr": ip_addr,
                "mcp_port": mcp_port,
                "novnc_port": novnc_port,
                "channel_port": channel_port,
                "http_port": http_port,
                "container_id": container_id,
            },
        }
    except ValueError as e:
        logger.warning("Create container rejected: %s", e)
        return {"status": "error", "message": str(e)}
    except:
        msg = f"Error create container: body={body}\n{traceback.format_exc()}"
        logger.error(msg)
        return {"status": "error", "message": msg}
    finally:
        reset_trace_id(token)


@app.post("/api/container/shutdown")
async def shutdown_container(request: Request, body: dict):
    token = set_trace_id(request.headers.get("TRACE_ID", "NA"))
    try:
        _ = body.get("token")
        container_id = body.get("container_id")
        logger.info(f"Shutdown container: container_id={container_id}")
        await container_server_manager.shutdown_mcp_server_container(container_id)
        logger.info(f"Container shutdown: container_id={container_id}")

        return {
            "status": "success",
            "message": f"MCP server shutdown: {container_id}",
        }
    except:
        msg = f"Error shutdown container: body={body}\n{traceback.format_exc()}"
        logger.error(msg)
        return {"status": "error", "message": msg}
    finally:
        reset_trace_id(token)


@app.post("/api/container/list")
async def list_containers(request: Request, body: dict):
    try:
        logger.info(f"List containers: body={body}")
        token = body.get("token")
        containers = await container_server_manager.list_containers()
        logger.info(f"List containers: containers={containers}")
        return {
            "status": "success",
            "data": containers,
        }
    except:
        msg = f"Error list container: body={body}\n{traceback.format_exc()}"
        logger.error(msg)
        return {"status": "error", "message": msg}


@app.get("/health")
async def health(request: Request):
    try:
        ok = await asyncio.wait_for(
            docker_helper.docker_ping_async(),
            timeout=docker_health_timeout_sec,
        )
    except (TimeoutError, asyncio.TimeoutError):
        ok = False
    if not ok:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "message": "docker_unreachable",
                "docker_ok": False,
            },
        )
    return {
        "status": "success",
        "message": "Container server is healthy",
        "docker_ok": True,
    }


if __name__ == "__main__":
    import uvicorn

    # Run the server
    uvicorn.run(app, host="0.0.0.0", port=container_server_port)
