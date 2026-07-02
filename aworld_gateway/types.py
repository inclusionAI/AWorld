from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class InboundEnvelope(BaseModel):
    channel: str
    account_id: str
    conversation_id: str
    conversation_type: Literal["dm", "group", "web"]
    sender_id: str
    sender_name: str | None = None
    message_id: str
    text: str
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OutboundEnvelope(BaseModel):
    channel: str
    account_id: str
    conversation_id: str
    reply_to_message_id: str | None = None
    text: str
    events: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
