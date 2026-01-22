"""WebSocket protocol implementation for env-channel."""

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import websockets
from websockets.server import WebSocketServerProtocol

from env_channel.common.exceptions import ConnectionError

logger = logging.getLogger(__name__)


class WebSocketConnection:
    """WebSocket connection wrapper."""

    def __init__(self, websocket: WebSocketServerProtocol):
        """
        Initialize WebSocket connection.

        Args:
            websocket: WebSocket server protocol instance
        """
        self.websocket = websocket
        self.remote_address = websocket.remote_address
        self.is_closed = False

    async def send(self, data: dict[str, Any]) -> None:
        """
        Send data through WebSocket.

        Args:
            data: Data to send (will be JSON serialized)
        """
        if self.is_closed:
            raise ConnectionError("WebSocket connection is closed")

        try:
            message = json.dumps(data)
            await self.websocket.send(message)
        except websockets.exceptions.ConnectionClosed:
            self.is_closed = True
            raise ConnectionError("WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            raise ConnectionError(f"Failed to send message: {e}")

    async def receive(self) -> dict[str, Any]:
        """
        Receive data from WebSocket.

        Returns:
            Received data (JSON deserialized)
        """
        if self.is_closed:
            raise ConnectionError("WebSocket connection is closed")

        try:
            message = await self.websocket.recv()
            if isinstance(message, str):
                return json.loads(message)
            return message
        except websockets.exceptions.ConnectionClosed:
            self.is_closed = True
            raise ConnectionError("WebSocket connection closed")
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON: {e}")
            raise ConnectionError(f"Invalid JSON message: {e}")
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
            raise ConnectionError(f"Failed to receive message: {e}")

    async def close(self) -> None:
        """Close WebSocket connection."""
        if not self.is_closed:
            self.is_closed = True
            await self.websocket.close()

    def __repr__(self) -> str:
        """String representation."""
        return f"WebSocketConnection(remote={self.remote_address}, closed={self.is_closed})"


async def create_client_connection(url: str) -> websockets.WebSocketClientProtocol:
    """
    Create WebSocket client connection.

    Args:
        url: WebSocket URL (e.g., "ws://localhost:8765")

    Returns:
        WebSocket client protocol instance
    """
    try:
        return await websockets.connect(url)
    except Exception as e:
        raise ConnectionError(f"Failed to connect to {url}: {e}")

