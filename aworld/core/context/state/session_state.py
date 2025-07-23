import uuid
from typing import Optional

from pydantic import BaseModel, Field

from aworld.memory.models import MemoryMessage


class SessionState(BaseModel):

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4().hex))

    # Process Log: Session History of actions and reasoning steps
    history_messages: Optional[list[MemoryMessage]]
