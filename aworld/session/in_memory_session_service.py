from typing import List, Optional
from typing_extensions import override
from datetime import datetime
from aworld.cmd import SessionModel, ChatCompletionMessage
from .base_session_service import BaseSessionService
import logging

logger = logging.getLogger(__name__)


class InMemorySessionService(BaseSessionService):
    def __init__(self):
        self.sessions = {}

    @override
    async def get_session(
        self, user_id: str, session_id: str
    ) -> Optional[SessionModel]:
        session_key = f"{user_id}:{session_id}"
        if session_key not in self.sessions:
            return None
        return self.sessions[session_key]

    @override
    async def list_sessions(self, user_id: str) -> List[SessionModel]:
        return [
            session
            for session_key, session in self.sessions.items()
            if session_key.startswith(user_id)
        ]

    @override
    async def create_session(
        self, user_id: str, session_id: str, name: str, description: str
    ) -> SessionModel:
        session_key = f"{user_id}:{session_id}"
        if session_key in self.sessions:
            raise ValueError(f"Session {session_id} already exists")
        self.sessions[session_key] = SessionModel(
            user_id=user_id,
            session_id=session_id,
            name=name,
            description=description,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            messages=[],
        )
        return self.sessions[session_key]

    @override
    async def delete_session(self, user_id: str, session_id: str) -> None:
        session_key = f"{user_id}:{session_id}"
        if session_key not in self.sessions:
            logger.warning(f"Session {session_key} not found")
        del self.sessions[session_key]

    @override
    async def append_messages(
        self, user_id: str, session_id: str, messages: List[ChatCompletionMessage]
    ) -> None:
        session_key = f"{user_id}:{session_id}"
        if session_key not in self.sessions:
            logger.info(f"Session {session_key} not found, create a new session")
            await self.create_session(
                user_id, session_id, messages[0].content, messages[0].content
            )
        self.sessions[session_key].messages.extend(messages)
        self.sessions[session_key].updated_at = datetime.now()
