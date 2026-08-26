import logging
import traceback
from fastapi import Request, WebSocket
import jwt

from ..utils.common_utils import get_remote_addr

from ..configs import token_secret

assert token_secret, "MCP_GATEWAY_TOKEN_SECRET is not set"


logger = logging.getLogger(__name__)


def check_mcp_auth(request: Request) -> bool:
    payload = get_auth_payload(dict(request.headers))
    logger.info(
        f"MCP Gateway auth: remote.addr={get_remote_addr(request)}, payload={payload}"
    )
    return payload is not None


def check_ws_auth(request: Request | WebSocket) -> bool:
    payload = get_auth_payload(dict(request.headers))
    if not payload:
        payload = get_auth_payload(dict(request.cookies))
    logger.info(
        f"WebSocket Gateway auth: remote.addr={get_remote_addr(request)}, payload={payload}"
    )
    return payload is not None


def get_auth_payload(headers: dict) -> str | None:
    """Check if the request is authorized"""
    try:
        token = headers.get("authorization") or headers.get("Authorization")
        if not token:
            return None

        token = token[len("Bearer ") :]
        if not token:
            return None
        payload = decode_token(token)
        return payload
    except BaseException as e:
        logger.error(f"Failed to get_auth_payload\n{traceback.format_exc()}")
        return None


def decode_token(token: str) -> str:
    """Decode the token"""
    payload = jwt.decode(token, token_secret, algorithms=["HS256"])
    return payload
