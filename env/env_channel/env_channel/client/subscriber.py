"""Channel Subscriber for receiving messages."""

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional

import websockets

from env_channel.common.exceptions import ConnectionError, MessageError
from env_channel.common.filter import MessageFilter
from env_channel.common.message import EnvChannelMessage
from env_channel.client.decorators import env_channel_sub, get_registered_handlers_grouped

logger = logging.getLogger(__name__)


class EnvChannelSubscriber:
    """Subscriber for receiving messages from Channel Server."""

    def __init__(
        self,
        server_url: str,
        reconnect_interval: float = 10.0,
        auto_reconnect: bool = True,
        auto_connect: bool = False,
        headers: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize Channel Subscriber.

        Args:
            server_url: WebSocket server URL (e.g., "ws://localhost:8765")
            reconnect_interval: Reconnection interval in seconds
            auto_reconnect: Whether to automatically reconnect on disconnect
            auto_connect: Whether to automatically connect on first subscribe
            headers: Optional HTTP headers for the WebSocket handshake,
                e.g. {"Authorization": "Bearer <token>"}.
            (no max retry limit; will retry indefinitely if enabled)
        """
        self.server_url = server_url
        self.reconnect_interval = reconnect_interval
        self.auto_reconnect = auto_reconnect
        self.auto_connect = auto_connect
        self.headers = headers
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._subscribed_topics: set[str] = set()
        self._handlers: list[tuple[Callable[[EnvChannelMessage], Any], Optional[MessageFilter]]] = []
        self._receive_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        logger.warning(f"subscriber init:{self.server_url}")

    async def connect(self) -> None:
        logger.warning("subscriber connect start")
        """Connect to the Channel Server."""
        if self._connected:
            logger.warning("Subscriber Already connected")
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
            logger.info(f"Subscriber Connected to Channel Server: {self.server_url}")
            logger.warning(f"Subscriber Connected success to Channel Server: {self.websocket}")

            # Resubscribe to previously subscribed topics
            if self._subscribed_topics:
                await self._subscribe_channels(list(self._subscribed_topics))

            # Start receiving messages
            self._receive_task = asyncio.create_task(self._receive_messages())
        except Exception as e:
            self._connected = False
            logger.warning("Subscriber Failed to connect to Channel Server %s: %s", self.server_url, e)
            raise ConnectionError(f"Subscriber Failed to connect to {self.server_url}: {e}")

    async def disconnect(self) -> None:
        """Disconnect from the Channel Server."""
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None

        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        self._connected = False
        logger.info("Subscriber Disconnected from Channel Server")

    async def subscribe(
        self,
        handler: Callable[[EnvChannelMessage], Any],
        topics: Optional[list[str]] = None,
        filter: Optional[MessageFilter] = None,
    ) -> None:
        """
        Subscribe to channels and set message handler.

        Args:
            topics: List of topic names to subscribe (None => ["default"])
            handler: Message handler function (async or sync)
            filter: Optional message filter
        """
        if (not self._connected) or (not self.websocket):
            if self.auto_connect:
                await self.connect()
            else:
                raise ConnectionError("Not connected to server")

        # record handler + optional filter
        self._handlers.append((handler, filter))
        topics = topics or ["default"]
        self._subscribed_topics.update(topics)

        await self._subscribe_channels(topics, filter)

    async def subscribe_handler(self, handler: Callable[[EnvChannelMessage], Any]) -> None:
        """
        Subscribe using a handler decorated with @env_channel_sub.

        The decorator should set __envchannel_topics and __envchannel_filter attributes.
        """
        topics = getattr(handler, "__envchannel_topics", None)
        filter_obj = getattr(handler, "__envchannel_filter", None)
        if topics is None:
            raise ValueError(
                "Handler is not decorated with @env_channel_sub (missing topics)."
            )
        await self.subscribe(
            handler=handler,
            topics=topics,
            filter=filter_obj,
        )

    async def subscribe_registered_handlers(self, server_url: str) -> None:
        """Subscribe all handlers registered via @env_channel_sub for a specific server_url."""
        grouped = get_registered_handlers_grouped()
        if server_url not in grouped:
            return
        for handler, topics, filter_obj in grouped[server_url]:
            await self.subscribe(handler=handler, topics=topics, filter=filter_obj)

    async def _subscribe_channels(self, topics: list[str], filter: Optional[MessageFilter] = None) -> None:
        """Subscribe to topics."""
        if not self.websocket:
            return

        subscribe_message = {
            "type": "subscribe",
            "topics": topics,
        }

        if filter:
            subscribe_message["filter"] = filter.to_dict()

        try:
            await self.websocket.send(json.dumps(subscribe_message))
            logger.info(f"Subscribed to topics: {topics}")
        except Exception as e:
            logger.warning(f"Error subscribing to channels: {e}")
            raise ConnectionError(f"Failed to subscribe: {e}")

    async def unsubscribe(self, topics: list[str]) -> None:
        """
        Unsubscribe from topics.

        Args:
            channels: List of channel names to unsubscribe
        """
        if not self._connected or not self.websocket:
            raise ConnectionError("Not connected to server")

        self._subscribed_topics.difference_update(topics)

        unsubscribe_message = {
            "type": "unsubscribe",
            "topics": topics,
        }

        try:
            await self.websocket.send(json.dumps(unsubscribe_message))
            logger.info(f"Unsubscribed from topics: {topics}")
        except Exception as e:
            logger.warning(f"Error unsubscribing from channels: {e}")
            raise ConnectionError(f"Failed to unsubscribe: {e}")

    async def _receive_messages(self) -> None:
        """Receive and process messages from server."""
        while self._connected and self.websocket:
            try:
                message_str = await self.websocket.recv()
                message_data = json.loads(message_str)

                # Handle server control messages
                if message_data.get("type") == "pong":
                    continue

                # Handle channel messages
                try:
                    message = EnvChannelMessage.from_dict(message_data)
                    await self._handle_message(message)
                except Exception as e:
                    logger.warning(f"Error processing message: {e}")

            except websockets.exceptions.ConnectionClosed:
                logger.warning("Connection closed")
                self._connected = False
                if self.auto_reconnect:
                    logger.info("Auto-reconnect enabled, starting reconnection...")
                    await self._reconnect()
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error receiving message: {e}")
                if self.auto_reconnect:
                    logger.info("Auto-reconnect enabled, starting reconnection...")
                    await self._reconnect()
                break

    async def _handle_message(self, message: EnvChannelMessage) -> None:
        """Handle a received message with all registered handlers."""
        for handler, handler_filter in self._handlers:
            if handler_filter and not handler_filter.matches(message):
                continue
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(message)
                else:
                    handler(message)
            except Exception as e:
                logger.warning(f"Error in message handler: {e}")

    async def _reconnect(self) -> None:
        """Reconnect to server with fixed interval (no limit)."""
        attempt = 0
        logger.info(f"Starting reconnection process (interval: {self.reconnect_interval}s)")
        while self.auto_reconnect:
            attempt += 1
            logger.info(
                f"Reconnecting in {self.reconnect_interval:.1f} seconds... (attempt {attempt})"
            )
            await asyncio.sleep(self.reconnect_interval)
            logger.info(f"Attempting to reconnect (attempt {attempt})...")
            try:
                # Reuse connect() which will continue to include headers
                await self.connect()
                logger.info(f"Reconnected successfully after {attempt} attempt(s)")
                return
            except Exception as e:
                logger.warning(f"Reconnection attempt {attempt} failed: {e}, will retry in {self.reconnect_interval}s")

    def is_connected(self) -> bool:
        """Check if connected to server."""
        return self._connected and self.websocket is not None


async def run_registered_subscribers_forever(
    auto_connect: bool = True,
    auto_reconnect: bool = True,
    reconnect_interval: float = 10.0,
    server_urls: Optional[list[str]] = None,
) -> None:
    """
    Start subscribers for all handlers registered via @env_channel_sub and keep listening.

    Args:
        auto_connect: auto connect on start
        auto_reconnect: auto reconnect on disconnect
        reconnect_interval: reconnect interval seconds
        server_urls: optional whitelist of server URLs to start; None means all registered
    """
    grouped = get_registered_handlers_grouped()
    targets = server_urls or list(grouped.keys())
    # Create subscribers for each target url
    subscribers: list[EnvChannelSubscriber] = []
    try:
        for url in targets:
            if url not in grouped:
                continue
            sub = EnvChannelSubscriber(
                server_url=url,
                auto_connect=auto_connect,
                auto_reconnect=auto_reconnect,
                reconnect_interval=reconnect_interval,
            )
            await sub.subscribe_registered_handlers(server_url=url)
            subscribers.append(sub)
            logger.info("Started subscriber for %s with %d handler(s)", url, len(grouped[url]))

        # keep running
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        for sub in subscribers:
            await sub.disconnect()
        logger.info("All subscribers stopped")
