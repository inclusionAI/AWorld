from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class InboundEnvelope(BaseModel):
    channel: str
    sender_id: Optional[str] = None
    session_id: Optional[str] = None
    text: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class OutboundEnvelope(BaseModel):
    channel: str
    recipient_id: Optional[str] = None
    agent_id: Optional[str] = None
    text: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
