import json
import logging
import traceback
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from ..utils.common_utils import check_server_health, get_remote_addr
from ..mcp_gateway import MCPGateway
from ..auth import check_auth

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/session")
async def session(request: Request):
    """Show session dashboard"""
    try:
        logger.info(f"Session dashboard, remote.addr={get_remote_addr(request)}")
        gateway: MCPGateway = request.app.state.gateway
        sessions = await gateway.session_connection_manager.get_sessions()
        session_list = []
        for session in sorted(
            sessions,
            key=lambda i: i.created_at.timestamp() if i and i.created_at else 0,
            reverse=True,
        ):
            i = session.model_dump(mode="json")
            i["created_at"] = (
                session.created_at.strftime("%Y/%m/%d %H:%M:%S")
                if session and session.created_at
                else ""
            )
            i["last_active_at"] = (
                session.last_active_at.strftime("%Y/%m/%d %H:%M:%S")
                if session and session.last_active_at
                else ""
            )
            session_list.append(i)
        status = {
            "status": "success",
            "data": {
                "total_count": len(session_list),
                "sessions": session_list,
            },
        }
        return Response(
            content=json.dumps(status, ensure_ascii=False, indent=2).encode("utf-8"),
            status_code=200,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    except BaseException as e:
        logger.error(
            f"Failed to get dashboard, remote.addr={get_remote_addr(request)}\n{traceback.format_exc()}"
        )
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Failed to get dashboard\n{traceback.format_exc()}",
            },
            status_code=500,
        )


@router.get("/container_server")
async def container_server(request: Request):
    """Show container server dashboard"""
    try:
        logger.info(
            f"Container server dashboard, remote.addr={get_remote_addr(request)}"
        )
        gateway = request.app.state.gateway
        container_servers = (
            await gateway.container_server_manager.get_container_servers()
        )
        container_server_list = []
        for server in container_servers:
            i = server.model_dump(exclude={"token"}, mode="json")
            i["health"] = await check_server_health(server.ip_addr, server.port)
            try:
                containers = await gateway.container_server_manager.list_containers(
                    server.server_id
                )
                container_list = []
                for c in containers:
                    if c.get("name", "").startswith("mcp_server_"):
                        c.pop("image_tags", None)
                        container_list.append(c)
                i["containers"] = container_list
            except:
                logger.error(
                    f"Failed to list containers of container server {server.server_id}\n{traceback.format_exc()}"
                )

            container_server_list.append(i)

        status = {
            "status": "success",
            "data": {
                "container_servers": container_server_list,
            },
        }
        return Response(
            content=json.dumps(status, ensure_ascii=False, indent=2).encode("utf-8"),
            status_code=200,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    except BaseException:
        logger.error(
            f"Failed to get container server dashboard, remote.addr={get_remote_addr(request)}\n{traceback.format_exc()}"
        )
        return JSONResponse(
            content={
                "status": "error",
                "message": "Failed to get container server dashboard",
            },
            status_code=500,
        )


@router.get("/remove/{container_id}")
async def remove_container(request: Request, container_id: str):
    """Remove container"""
    try:
        if not check_auth.check_ws_auth(request):
            logger.warning(
                f"Remove container unauthorized! remote.addr={get_remote_addr(request)}, request.headers={request.headers}"
            )
            return Response(status_code=401, content="Unauthorized")

        logger.info(
            f"Remove container, remote.addr={get_remote_addr(request)}, container_id={container_id}"
        )
        gateway: MCPGateway = request.app.state.gateway
        await gateway.session_connection_manager.remove_container(container_id)

        status = {"status": "success"}
        return Response(
            content=json.dumps(status, ensure_ascii=False, indent=2).encode("utf-8"),
            status_code=200,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    except BaseException:
        logger.error(
            f"Failed to remove container, remote.addr={get_remote_addr(request)}\n{traceback.format_exc()}"
        )
        return JSONResponse(
            content={
                "status": "error",
                "message": "Failed to remove container",
            },
            status_code=500,
        )
