import uuid
from datetime import datetime
from typing import Dict, Optional, List

from aworld.cmd.data_model import SessionModel, ChatCompletionMessage
from aworld.session.base_session_service import BaseSessionService
from aworld.experimental.bidi_streaming.transport import Transport


class BidiSession(SessionModel):
    """Session for bidirectional streaming."""
    is_running: bool = False
    transport: Transport = None


class InMemoryBidiSessionService(BaseSessionService):
    """Session service for bidirectional streaming."""

    def __init__(self):
        self._sessions: Dict[str, BidiSession] = {}

    async def get_session(
        self, user_id: str, session_id: str
    ) -> Optional[BidiSession]:
        session_key = f"{user_id}:{session_id}"
        return self._sessions.get(session_key)

    async def list_sessions(self, user_id: str) -> List[BidiSession]:
        return [
            session
            for key, session in self._sessions.items()
            if key.startswith(user_id)
        ]

    async def delete_session(self, user_id: str, session_id: str) -> None:
        session_key = f"{user_id}:{session_id}"
        if session_key in self._sessions:
            del self._sessions[session_key]

    async def append_messages(
        self, user_id: str, session_id: str, messages: List[ChatCompletionMessage]
    ) -> None:
        session_key = f"{user_id}:{session_id}"
        session = self._sessions.get(session_key)
        if session:
            session.messages.extend(messages)

    async def create_session(self,
                             user_id: str,
                             transport: Transport,
                             session_id: Optional[str] = None,
                             name: Optional[str] = None,
                             description: Optional[str] = None,
                             ) -> BidiSession:
        if session_id:
            session_key = f"{user_id}:{session_id}"
            if session_key in self._sessions:
                raise ValueError(f"Session {session_id} already exists")
        else:
            session_key = f"{user_id}:{uuid.uuid4().hex}"

        await transport.connect()
        session = BidiSession(
            user_id=user_id,
            session_id=session_id,
            name=name,
            description=description,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            messages=[],
            transport=transport,
            is_running=True,
        )
        self._sessions[session_key] = session
        return session
