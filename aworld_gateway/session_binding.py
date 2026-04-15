from __future__ import annotations

from pydantic import BaseModel


class SessionBinding(BaseModel):
    agent_id: str
    channel: str
    account_id: str
    conversation_type: str
    conversation_id: str

    def build(self) -> str:
        return (
            f"gw:{self.agent_id}:{self.channel}:{self.account_id}:"
            f"{self.conversation_type}:{self.conversation_id}"
        )
