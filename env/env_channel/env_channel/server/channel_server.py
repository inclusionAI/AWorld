"""Channel Server implementation."""

import asyncio
import logging
import traceback
from typing import Optional
from uuid import uuid4

import websockets
from websockets.asyncio.server import ServerConnection

# Use custom ConnectionError to avoid confusion with built-in ConnectionError
from env_channel.common.exceptions import ServerError, ConnectionError as ChannelConnectionError
from env_channel.common.message import EnvChannelMessage, ServerMessage
from env_channel.server.connection_manager import ConnectionManager
from env_channel.protocol.websocket import WebSocketConnection

logger = logging.getLogger(__name__)


class EnvChannelServer:
    """WebSocket Channel Server for message broadcasting."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        ping_interval: Optional[float] = 20.0,
        ping_timeout: Optional[float] = 10.0,
    ):
        """
        Initialize Channel Server.

        Args:
            host: Server host address
            port: Server port
            ping_interval: WebSocket ping interval in seconds
            ping_timeout: WebSocket ping timeout in seconds
        """
        self.host = host
        self.port = port
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.connection_manager = ConnectionManager()
        self.server: Optional[websockets.Serve] = None
        self._running = False

    async def start(self) -> None:
        """Start the WebSocket server."""
        if self._running:
            logger.warning("Server is already running")
            return

        self.server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
        )
        self._running = True
        logger.info(f"Channel Server started on ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if not self._running:
            return

        self._running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logger.info("Channel Server stopped")

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        """
        Handle a new WebSocket connection.

        Args:
            websocket: WebSocket server protocol instance
        """
        path = websocket.request.path
        if path != "/channel":
            logger.warning(f"Rejected connection with invalid path: {path}")
            await websocket.close(code=4004, reason="Invalid path")
            return

        connection_id = str(uuid4())
        connection = WebSocketConnection(websocket)
        self.connection_manager.add_connection(connection_id, connection)

        try:
            await self._handle_messages(connection_id, connection)
        except BaseException as e:
            logger.error(f"Error handling connection {connection_id}: {e}")
        finally:
            self.connection_manager.remove_connection(connection_id)
            await connection.close()

    async def _handle_messages(self, connection_id: str, connection: WebSocketConnection) -> None:
        """
        Handle messages from a connection.

        Args:
            connection_id: Connection identifier
            connection: WebSocket connection
        """
        while True:
            try:
                data = await connection.receive()
                await self._process_message(connection_id, data)
            except ChannelConnectionError as e:
                # Connection closed by client or network error, break the loop
                logger.info(f"Connection {connection_id} closed: {e}")
                break
            except BaseException as e:
                # Log error but continue processing (don't break on message format errors)
                logger.error(f"Error processing message from {connection_id}: {e}")
                # Continue to next message instead of breaking
                continue

    async def _process_message(self, connection_id: str, data: dict) -> None:
        """
        Process a message from a connection.

        Args:
            connection_id: Connection identifier
            data: Message data
        """
        # Log raw data for debugging client messages
        logger.info("Received raw data from %s: %s", connection_id, data)

        try:
            message = ServerMessage(**data)
        except BaseException:
            logger.error(f"Invalid message format: {traceback.format_exc()}")
            return

        # Log parsed control message with type / topics / data etc.
        # logger.info(
        #     "Processing message from %s: type=%s topics=%s data=%s",
        #     connection_id,
        #     message.type,
        #     getattr(message, "topics", None),
        #     getattr(message, "data", None),
        # )

        if message.type == "subscribe":
            if message.topics:
                # Log subscription request (connection ID and subscribed topic list)
                logger.info(
                    "Connection %s subscribed to topics: %s",
                    connection_id,
                    message.topics,
                )
                self.connection_manager.subscribe(connection_id, message.topics)
        elif message.type == "unsubscribe":
            if message.topics:
                self.connection_manager.unsubscribe(connection_id, message.topics)
        elif message.type == "publish":
            # Handle publish from publisher (internal publish)
            if message.data:
                topic = message.data.get("topic", "default")
                channel_message = EnvChannelMessage(
                    topic=topic,
                    message=message.data.get("message", {}),
                    metadata=message.data.get("metadata", {}),
                    tags=message.data.get("tags", []),
                )
                await self.connection_manager.broadcast(channel_message)
            else:
                logger.warning(
                    "Received publish message without 'data' field from %s. "
                    "Expected format: {'type': 'publish', 'data': {'topic': ..., 'message': ...}}",
                    connection_id,
                )
        elif message.type == "ping":
            # Respond to ping
            connection = self.connection_manager.connections.get(connection_id)
            if connection:
                await connection.send({"type": "ping"})

    async def publish(self, message: EnvChannelMessage) -> None:
        """
        Publish a message to all subscribers.

        Args:
            message: Message to publish
        """
        await self.connection_manager.broadcast(message)

    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running

    def get_stats(self) -> dict:
        """Get server statistics."""
        return {
            "running": self._running,
            "connections": self.connection_manager.get_connection_count(),
            "channels": self.connection_manager.get_channel_count(),
        }
