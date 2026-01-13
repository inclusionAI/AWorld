"""Channel Publisher for sending messages."""

import asyncio
import json
import logging
from typing import Any, Dict, Optional

import websockets

from env_channel.common.exceptions import ConnectionError, MessageError
from env_channel.common.message import EnvChannelMessage

logger = logging.getLogger(__name__)


class EnvChannelPublisher:
    """Publisher for sending messages to Channel Server."""

    def __init__(
        self,
        server_url: str,
        reconnect_interval: float = 5.0,
        auto_connect: bool = False,
        auto_reconnect: bool = True,
        max_retries: int = 3,
        headers: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize Channel Publisher.

        Args:
            server_url: WebSocket server URL (e.g., "ws://localhost:8765")
            reconnect_interval: Reconnection interval in seconds
            auto_connect: If True, first publish will auto-connect (can skip explicit connect)
            auto_reconnect: If True, will attempt to reconnect on send failure
            max_retries: Max retry attempts when auto_reconnect is enabled
            headers: Optional HTTP headers for the WebSocket handshake,
                e.g. {"Authorization": "Bearer <token>"}.
        """
        self.server_url = server_url
        self.reconnect_interval = reconnect_interval
        self.auto_connect = auto_connect
        self.auto_reconnect = auto_reconnect
        self.max_retries = max_retries
        self.headers = headers
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._reconnect_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        """Connect to the Channel Server."""
        if self._connected:
            logger.warning("Already connected")
            return

        try:
            # Support custom handshake headers, e.g., Authorization: Bearer <token>
            if self.headers:
                self.websocket = await websockets.connect(
                    self.server_url, additional_headers=self.headers
                )
            else:
                self.websocket = await websockets.connect(self.server_url)
            self._connected = True
            logger.info(f"Publisher Connected to Channel Server: {self.server_url}")
        except Exception as e:
            self._connected = False
            logger.warning("Publisher Failed to connect to Channel Server %s: %s", self.server_url, e)
            raise ConnectionError(f"Publisher Failed to connect to {self.server_url}: {e}")

    async def disconnect(self) -> None:
        """Disconnect from the Channel Server."""
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        self._connected = False
        logger.info("Disconnected from Channel Server")

    async def publish(
        self,
        message: Dict[str, Any],
        topic: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[list[str]] = None,
    ) -> None:
        """
        Publish a message to a topic.
        """
        await self._ensure_connected()

        attempt = 0
        while True:
            try:
                # Validate message
                EnvChannelMessage(
                    topic=topic,
                    message=message,
                    metadata=metadata or {},
                    tags=tags or [],
                )

                publish_message = {
                    "type": "publish",
                    "data": {
                        "topic": topic,
                        "message": message,
                        "metadata": metadata or {},
                        "tags": tags or [],
                    },
                }

                await self.websocket.send(json.dumps(publish_message))
                logger.debug(f"Published message to topic: {topic}")
                return
            except websockets.exceptions.ConnectionClosed as e:
                self._connected = False
                attempt += 1
                if not self.auto_reconnect or attempt > self.max_retries:
                    raise ConnectionError(f"Connection closed: {e}")
                logger.warning(
                    f"Connection closed, retrying ({attempt}/{self.max_retries})..."
                )
                await asyncio.sleep(self.reconnect_interval)
                await self._reconnect()
            except Exception as e:
                logger.error(f"Error publishing message: {e}")
                raise MessageError(f"Failed to publish message: {e}")

    async def _ensure_connected(self) -> None:
        """Ensure connection is active; auto-connect if enabled."""
        if self._connected and self.websocket:
            return
        if self.auto_connect:
            await self.connect()
        else:
            raise ConnectionError("Not connected to server")

    async def _reconnect(self) -> None:
        """Reconnect to server."""
        try:
            if self.headers:
                self.websocket = await websockets.connect(
                    self.server_url, additional_headers=self.headers
                )
            else:
                self.websocket = await websockets.connect(self.server_url)
            self._connected = True
            logger.info(f"Reconnected to Channel Server: {self.server_url}")
        except Exception as e:
            self._connected = False
            logger.warning(
                "Failed to reconnect to Channel Server %s: %s", self.server_url, e
            )
            raise ConnectionError(f"Failed to reconnect to {self.server_url}: {e}")

    def is_connected(self) -> bool:
        """Check if connected to server."""
        return self._connected and self.websocket is not None

