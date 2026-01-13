"""Message models for env-channel."""

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class EnvChannelMessage(BaseModel):
    """Topic message model."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    topic: str = Field(default="default", description="Topic name (default: default)")
    message_type: str = Field(default="message", description="Message type")
    message: Dict[str, Any] = Field(..., description="Message payload")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Message metadata")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Message timestamp")
    tags: list[str] = Field(default_factory=list, description="Message tags for filtering")

    class Config:
        """Pydantic config."""

        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary."""
        # Ensure datetime and other types are JSON serializable
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EnvChannelMessage":
        """Create message from dictionary."""
        if isinstance(data.get("timestamp"), str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


class ServerMessage(BaseModel):
    """Server control message."""

    type: str = Field(..., description="Message type (subscribe, unsubscribe, ping, pong)")
    topics: Optional[list[str]] = Field(None, description="Topic names")
    filter: Optional[Dict[str, Any]] = Field(None, description="Message filter")
    data: Optional[Dict[str, Any]] = Field(None, description="Additional data")


