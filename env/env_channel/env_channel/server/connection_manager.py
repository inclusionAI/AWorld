"""Connection manager for Channel Server."""

import asyncio
import logging
from typing import Dict, Set

from env_channel.common.message import EnvChannelMessage
from env_channel.protocol.websocket import WebSocketConnection

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and subscriptions."""

    def __init__(self):
        """Initialize connection manager."""
        # Map of connection_id -> WebSocketConnection
        self.connections: Dict[str, WebSocketConnection] = {}
        # Map of topic -> Set of connection_ids
        self.topic_subscriptions: Dict[str, Set[str]] = {}
        # Map of connection_id -> Set of topics
        self.connection_topics: Dict[str, Set[str]] = {}

    def add_connection(self, connection_id: str, connection: WebSocketConnection) -> None:
        """
        Add a new connection.

        Args:
            connection_id: Unique connection identifier
            connection: WebSocket connection
        """
        self.connections[connection_id] = connection
        self.connection_topics[connection_id] = set()
        logger.info(f"Connection added: {connection_id}")

    def remove_connection(self, connection_id: str) -> None:
        """
        Remove a connection and clean up subscriptions.

        Args:
            connection_id: Connection identifier to remove
        """
        if connection_id in self.connections:
            # Remove from topic subscriptions
            topics = self.connection_topics.get(connection_id, set())
            for topic in topics:
                if topic in self.topic_subscriptions:
                    self.topic_subscriptions[topic].discard(connection_id)
                    if not self.topic_subscriptions[topic]:
                        del self.topic_subscriptions[topic]

            # Remove connection
            del self.connections[connection_id]
            if connection_id in self.connection_topics:
                del self.connection_topics[connection_id]
            logger.info(f"Connection removed: {connection_id}")

    def subscribe(self, connection_id: str, topics: list[str]) -> None:
        """
        Subscribe connection to topics.

        Args:
            connection_id: Connection identifier
            topics: List of topic names to subscribe
        """
        if connection_id not in self.connections:
            raise ValueError(f"Connection {connection_id} not found")

        for topic in topics:
            if topic not in self.topic_subscriptions:
                self.topic_subscriptions[topic] = set()
            self.topic_subscriptions[topic].add(connection_id)
            self.connection_topics[connection_id].add(topic)

        logger.info(f"Connection {connection_id} subscribed to topics: {topics}")

    def unsubscribe(self, connection_id: str, topics: list[str]) -> None:
        """
        Unsubscribe connection from topics.

        Args:
            connection_id: Connection identifier
            topics: List of topic names to unsubscribe
        """
        for topic in topics:
            if topic in self.topic_subscriptions:
                self.topic_subscriptions[topic].discard(connection_id)
                if not self.topic_subscriptions[topic]:
                    del self.topic_subscriptions[topic]

            if connection_id in self.connection_topics:
                self.connection_topics[connection_id].discard(topic)

        logger.info(f"Connection {connection_id} unsubscribed from topics: {topics}")

    def get_subscribers(self, topic: str) -> Set[str]:
        """
        Get all connection IDs subscribed to a topic.

        Args:
            topic: Topic name

        Returns:
            Set of connection IDs
        """
        return self.topic_subscriptions.get(topic, set())

    async def broadcast(self, message: EnvChannelMessage) -> None:
        """
        Broadcast message to all subscribers of the topic.

        Args:
            message: Message to broadcast
        """
        subscribers = self.get_subscribers(message.topic)
        if not subscribers:
            logger.warning(f"No subscribers for topic: {message.topic}")
            return

        # Send message to all subscribers
        tasks = []
        for connection_id in subscribers:
            if connection_id in self.connections:
                connection = self.connections[connection_id]
                tasks.append(self._send_to_connection(connection, message))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Log any errors
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Error sending to subscriber: {result}")

    async def _send_to_connection(self, connection: WebSocketConnection, message: EnvChannelMessage) -> None:
        """
        Send message to a specific connection.

        Args:
            connection: WebSocket connection
            message: Message to send
        """
        try:
            await connection.send(message.to_dict())
        except Exception as e:
            logger.error(f"Error sending message to connection: {e}")
            raise

    def get_connection_count(self) -> int:
        """Get total number of connections."""
        return len(self.connections)

    def get_channel_count(self) -> int:
        """Get total number of topics with subscribers."""
        return len(self.topic_subscriptions)

