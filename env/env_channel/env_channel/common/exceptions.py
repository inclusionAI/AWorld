"""Exceptions for env-channel."""


class ChannelError(Exception):
    """Base exception for env-channel errors."""

    pass


class ConnectionError(ChannelError):
    """Connection-related errors."""

    pass


class MessageError(ChannelError):
    """Message-related errors."""

    pass


class ServerError(ChannelError):
    """Server-related errors."""

    pass


class FilterError(ChannelError):
    """Filter-related errors."""

    pass

