import logging
from typing import List
from fastapi import APIRouter, Depends
from aworld.cmd import SessionModel
from aworld.cmd.utils.agent_server import CURRENT_SERVER
from aworld.cmd.web.web_server import get_user_id_from_jwt

logger = logging.getLogger(__name__)

router = APIRouter()

prefix = "/api/session"


@router.get("/list")
async def list_sessions(
    user_id: str = Depends(get_user_id_from_jwt),
) -> List[SessionModel]:
    return await CURRENT_SERVER.get_session_service().list_sessions(user_id)


@router.post("/delete")
async def delete_session(
    session_id: str, user_id: str = Depends(get_user_id_from_jwt)
) -> None:
    return await CURRENT_SERVER.get_session_service().delete_session(
        user_id, session_id
    )
