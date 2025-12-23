"""
env-channel: Bidirectional WebSocket channel SDK for MCP servers and Agent clients.
"""

__version__ = "0.1.0"

# Export main API
from env_channel.client import EnvChannelPublisher, EnvChannelSubscriber, env_channel_sub
from env_channel.common import EnvChannelMessage, MessageFilter
from env_channel.common.exceptions import (
    ChannelError,
    MessageError,
)

# Note: ConnectionError is a built-in exception, so we use ChannelConnectionError
# Import it with an alias to avoid conflicts
from env_channel.common.exceptions import ConnectionError as ChannelConnectionError

__all__ = [
    "EnvChannelPublisher",
    "EnvChannelSubscriber",
    "env_channel_sub",
    "EnvChannelMessage",
    "MessageFilter",
    "ChannelError",
    "ChannelConnectionError",
    "MessageError",
]

