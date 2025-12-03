import abc
import asyncio

from typing import Any, List
from dataclasses import dataclass, field, asdict
from enum import Enum
import uuid
from fastapi import WebSocket, WebSocketDisconnect

from aworld.core.event.base import Message
from aworld.logs.util import logger


class BidiEventType(Enum):
    CONFIG_EVENT = "config"
    INPUT_EVENT = "input"
    OUTPUT_EVENT = "output"
    TOOL_EVENT = "tool"
    LLM_CHUNK = "llm_chunk"
    ERROR_EVENT = "error"
    END_EVENT = "end"


@dataclass
class BidiEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: BidiEventType
    data: Any = field(default=None)
    timestamp: float = field(default_factory=asyncio.get_event_loop().time)


@dataclass
class BidiMessage(Message[BidiEvent]):
    category: str = 'bidi'

    def __post_init__(self):
        if isinstance(self.payload, BidiEvent):
            self.topic = self.payload.event_type.value


class Transport(abc.ABC):

    @abc.abstractmethod
    async def connect(self):
        """Connect to the server."""
        raise NotImplementedError()

    async def receive(self) -> BidiMessage:
        """Receive an event from the client."""
        raise NotImplementedError()

    async def send(self, message: BidiMessage):
        """Send an event to the client."""
        raise NotImplementedError()


class WebSocketTransport(Transport):
    """Transport for WebSocket."""

    def __init__(self, websocket: WebSocket):
        self.ws = websocket
        self.is_connected = False

    async def connect(self):
        await self.ws.accept()
        self.is_connected = True

    async def receive(self) -> BidiMessage:
        """Receive an event from the client."""
        try:
            msg = await self.ws.receive_json()
            if 'event_type' in msg:
                event = BidiEvent(**msg)
            else:
                event = BidiEvent(
                    event_type=BidiEventType.INPUT_EVENT,
                    data=msg
                )
            return BidiMessage(payload=event)
        except WebSocketDisconnect:
            self.is_connected = False
            raise
        except Exception as e:
            logger.error(f"Receive error: {e}")
            return None
        return None

    async def send(self, message: BidiMessage):
        """Send an event to the client."""
        try:
            if isinstance(message.payload, BidiEvent):
                await self.ws.send_json(asdict(message.payload))
            else:
                event = BidiEvent(
                    event_type=BidiEventType.OUTPUT_EVENT,
                    data=message.payload
                )
                await self.ws.send_json(asdict(event))
        except Exception as e:
            logger.error(f"Send error: {e}")

    async def close(self):
        try:
            await self.ws.close()
        except Exception as e:
            logger.error(f"Close error: {e}")
        finally:
            self.is_connected = False
