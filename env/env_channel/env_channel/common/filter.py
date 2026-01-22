"""Message filtering for env-channel."""

from typing import Any, Callable, Dict, Optional

from env_channel.common.message import EnvChannelMessage


class MessageFilter:
    """Message filter for channel subscriptions."""

    def __init__(
        self,
        topics: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
        custom_filter: Optional[Callable[[EnvChannelMessage], bool]] = None,
    ):
        """
        Initialize message filter.

        Args:
            topics: List of topic names to filter (None means all topics)
            tags: List of tags to filter (message must have at least one tag)
            custom_filter: Custom filter function
        """
        self.topics = topics
        self.tags = tags
        self.custom_filter = custom_filter

    def matches(self, message: EnvChannelMessage) -> bool:
        """
        Check if message matches the filter.

        Args:
            message: Message to check

        Returns:
            True if message matches the filter
        """
        # Filter by topics
        if self.topics is not None and message.topic not in self.topics:
            return False

        # Filter by tags
        if self.tags is not None:
            if not any(tag in message.tags for tag in self.tags):
                return False

        # Custom filter
        if self.custom_filter is not None:
            if not self.custom_filter(message):
                return False

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert filter to dictionary."""
        return {
            "topics": self.topics,
            "tags": self.tags,
            "has_custom_filter": self.custom_filter is not None,
        }

