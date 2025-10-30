# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Distributed streaming queue system for AWorld framework.

This module provides an abstraction layer for streaming message queues that can work
in both single-process and distributed environments.
"""
import abc
import asyncio
import json
from typing import Any, Dict, Optional, AsyncIterator
from dataclasses import dataclass, field

from aworld.core.event.base import Message
from aworld.logs.util import logger


@dataclass
class StreamingQueueConfig:
    """Configuration for streaming queue.
    
    Attributes:
        backend: Queue backend type. Options: 'inmemory', 'redis', or 'custom'
        queue_id: Optional queue identifier. If None, will be auto-generated.
        timeout: Timeout for blocking operations in seconds.
        max_size: Maximum queue size (0 = unlimited).
        custom_cls: Full class path for custom queue implementation (e.g., 'myapp.queues.MyQueue').
                   Only used when backend='custom'.
        redis: Redis-specific configuration.
    """
    backend: str = 'inmemory'
    queue_id: Optional[str] = None
    timeout: int = 60
    max_size: int = 0
    custom_cls: Optional[str] = None  # For user-defined queue implementations
    
    # Backend-specific configs
    redis: Dict[str, Any] = field(default_factory=lambda: {
        'host': '127.0.0.1',
        'port': 6379,
        'db': 0,
        'password': None,
        'prefix': 'aworld:stream:'
    })


class StreamingQueueProvider(abc.ABC):
    """Abstract base class for streaming queue providers."""
    
    def __init__(self, config: StreamingQueueConfig):
        """Initialize the queue provider.
        
        Args:
            config: Queue configuration.
        """
        self.config = config
        self.queue_id = config.queue_id
        
    @abc.abstractmethod
    async def put(self, message: Message) -> None:
        """Put a message into the queue.
        
        Args:
            message: Message to enqueue.
        """
        pass
    
    @abc.abstractmethod
    async def get(self, timeout: Optional[float] = None) -> Message:
        """Get a message from the queue (blocking).
        
        Args:
            timeout: Timeout in seconds. None means use default config timeout.
            
        Returns:
            Message from the queue.
            
        Raises:
            asyncio.TimeoutError: If timeout is reached.
        """
        pass
    
    @abc.abstractmethod
    async def close(self) -> None:
        """Close the queue and cleanup resources."""
        pass
    
    @abc.abstractmethod
    async def is_closed(self) -> bool:
        """Check if the queue is closed."""
        pass
    
    def get_queue_id(self) -> str:
        """Get the queue identifier."""
        return self.queue_id


class InMemoryStreamingQueue(StreamingQueueProvider):
    """In-memory streaming queue for single-process scenarios."""
    
    def __init__(self, config: StreamingQueueConfig):
        """Initialize in-memory queue.
        
        Args:
            config: Queue configuration.
        """
        super().__init__(config)
        max_size = config.max_size if config.max_size > 0 else 0
        self._queue = asyncio.Queue(maxsize=max_size)
        self._closed = False
        
    async def put(self, message: Message) -> None:
        """Put a message into the queue.
        
        Args:
            message: Message to enqueue.
            
        Raises:
            RuntimeError: If queue is closed.
        """
        if self._closed:
            raise RuntimeError("Cannot put message to closed queue")
        await self._queue.put_nowait(message)
        
    async def get(self, timeout: Optional[float] = None) -> Message:
        """Get a message from the queue (blocking).
        
        Args:
            timeout: Timeout in seconds. None means use default config timeout.
            
        Returns:
            Message from the queue.
            
        Raises:
            asyncio.TimeoutError: If timeout is reached.
        """
        if timeout is None:
            timeout = self.config.timeout
            
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Queue get timeout after {timeout}s")
            raise
    
    async def close(self) -> None:
        """Close the queue."""
        self._closed = True
        
    async def is_closed(self) -> bool:
        """Check if the queue is closed."""
        return self._closed


class RedisStreamingQueue(StreamingQueueProvider):
    """Redis-based streaming queue for distributed scenarios."""
    
    def __init__(self, config: StreamingQueueConfig):
        """Initialize Redis streaming queue.
        
        Args:
            config: Queue configuration.
        """
        super().__init__(config)
        self._redis_client = None
        self._closed = False
        self._last_id = "0"
        self._stream_key = f"{config.redis['prefix']}{self.queue_id}"
        
    async def _ensure_connection(self):
        """Ensure Redis connection is established."""
        if self._redis_client is None:
            try:
                import redis.asyncio as redis
            except ImportError:
                raise ImportError(
                    "redis package is required for RedisStreamingQueue. "
                    "Install it with: pip install redis"
                )
            
            redis_conf = self.config.redis
            self._redis_client = redis.Redis(
                host=redis_conf['host'],
                port=redis_conf['port'],
                db=redis_conf['db'],
                password=redis_conf['password'],
                decode_responses=False  # We'll handle encoding ourselves
            )
            logger.info(f"Redis streaming queue connected: {self._stream_key}")
    
    async def put(self, message: Message) -> None:
        """Put a message into Redis stream.
        
        Args:
            message: Message to enqueue.
            
        Raises:
            RuntimeError: If queue is closed.
        """
        if self._closed:
            raise RuntimeError("Cannot put message to closed queue")
            
        await self._ensure_connection()
        
        # Serialize message to dict
        message_data = self._serialize_message(message)
        
        # Add to Redis stream
        await self._redis_client.xadd(
            self._stream_key,
            message_data,
            maxlen=self.config.max_size if self.config.max_size > 0 else None
        )
        
    async def get(self, timeout: Optional[float] = None) -> Message:
        """Get a message from Redis stream (blocking).
        
        Args:
            timeout: Timeout in seconds. None means use default config timeout.
            
        Returns:
            Message from the stream.
            
        Raises:
            asyncio.TimeoutError: If timeout is reached.
        """
        if timeout is None:
            timeout = self.config.timeout
            
        await self._ensure_connection()
        
        # Convert timeout to milliseconds for Redis XREAD
        block_ms = int(timeout * 1000)
        
        try:
            # XREAD with blocking
            result = await asyncio.wait_for(
                self._redis_client.xread(
                    {self._stream_key: self._last_id},
                    count=1,
                    block=block_ms
                ),
                timeout=timeout + 1  # Add buffer to asyncio timeout
            )
            
            if not result:
                raise asyncio.TimeoutError(f"No message received in {timeout}s")
            
            # Parse result: [('stream_key', [('id', {'field': 'value'})])]
            stream_messages = result[0][1]
            if not stream_messages:
                raise asyncio.TimeoutError(f"No message received in {timeout}s")
            
            msg_id, msg_data = stream_messages[0]
            self._last_id = msg_id
            
            # Deserialize message
            return self._deserialize_message(msg_data)
            
        except asyncio.TimeoutError:
            logger.warning(f"Redis queue get timeout after {timeout}s")
            raise
    
    async def close(self) -> None:
        """Close the Redis connection."""
        self._closed = True
        if self._redis_client:
            await self._redis_client.close()
            logger.info(f"Redis streaming queue closed: {self._stream_key}")
    
    async def is_closed(self) -> bool:
        """Check if the queue is closed."""
        return self._closed
    
    def _serialize_message(self, message: Message) -> Dict[bytes, bytes]:
        """Serialize Message to Redis-compatible format.
        
        Args:
            message: Message to serialize.
            
        Returns:
            Dict with byte keys and values.
        """
        # Convert Message to dict (assuming Message has a to_dict method or is serializable)
        try:
            if hasattr(message, 'model_dump'):
                msg_dict = message.model_dump()
            elif hasattr(message, 'to_dict'):
                msg_dict = message.to_dict()
            else:
                msg_dict = vars(message)
            
            # Serialize to JSON string then to bytes
            json_str = json.dumps(msg_dict, default=str)
            return {b'data': json_str.encode('utf-8')}
        except Exception as e:
            logger.error(f"Failed to serialize message: {e}")
            raise
    
    def _deserialize_message(self, data: Dict[bytes, bytes]) -> Message:
        """Deserialize Redis data to Message.
        
        Args:
            data: Redis stream entry data.
            
        Returns:
            Deserialized Message.
        """
        try:
            json_str = data[b'data'].decode('utf-8')
            msg_dict = json.loads(json_str)
            
            # Reconstruct Message object
            return Message(**msg_dict)
        except Exception as e:
            logger.error(f"Failed to deserialize message: {e}")
            raise




def build_streaming_queue(config: StreamingQueueConfig) -> StreamingQueueProvider:
    """Factory function to build streaming queue based on configuration.
    
    Args:
        config: Streaming queue configuration.
        
    Returns:
        StreamingQueueProvider instance.
        
    Raises:
        ValueError: If backend type is not supported or custom_cls is invalid.
        
    Examples:
        # Built-in backend
        config = StreamingQueueConfig(backend='redis')
        queue = build_streaming_queue(config)
        
        # Custom backend
        config = StreamingQueueConfig(
            backend='custom',
            custom_cls='myapp.queues.KafkaStreamingQueue'
        )
        queue = build_streaming_queue(config)
    """
    from aworld.utils.common import new_instance
    
    backend = config.backend.lower()
    
    if backend == 'inmemory':
        return InMemoryStreamingQueue(config)
    elif backend == 'redis':
        return RedisStreamingQueue(config)
    elif backend == 'custom':
        # User-defined custom queue implementation
        if not config.custom_cls:
            raise ValueError(
                "When backend='custom', you must provide 'custom_cls' parameter "
                "with the full class path to your queue implementation."
            )
        try:
            return new_instance(config.custom_cls, config)
        except Exception as e:
            raise ValueError(
                f"Failed to instantiate custom streaming queue '{config.custom_cls}': {e}"
            )
    else:
        raise ValueError(
            f"Unsupported streaming queue backend: {backend}. "
            f"Supported backends: 'inmemory', 'redis', 'custom'"
        )


async def create_streaming_queue_from_dict(config_dict: Dict[str, Any]) -> StreamingQueueProvider:
    """Create streaming queue from configuration dictionary.
    
    Args:
        config_dict: Configuration dictionary.
        
    Returns:
        StreamingQueueProvider instance.
        
    Example:
        config = {
            'backend': 'redis',
            'queue_id': 'my-task-123',
            'redis': {
                'host': 'localhost',
                'port': 6379
            }
        }
        queue = await create_streaming_queue_from_dict(config)
    """
    config = StreamingQueueConfig(**config_dict)
    return build_streaming_queue(config)

