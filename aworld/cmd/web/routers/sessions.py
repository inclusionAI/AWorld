import logging
from typing import List
from fastapi import APIRouter
from aworld.cmd import SessionModel

logger = logging.getLogger(__name__)

router = APIRouter()

prefix = "/api/session"


@router.get("/list")
async def list_sessions(user_id: str) -> List[SessionModel]:
    pass


@router.post("/delete")
async def delete_session(user_id: str, session_id: str) -> None:
    pass
