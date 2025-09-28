from abc import ABC
from typing import Optional


class LoggerProvider(ABC):
    """A logger provider is a factory for loggers."""


_GLOBAL_LOG_PROVIDER: Optional[LoggerProvider] = None


def set_log_provider(provider: str = "otlp",
                     backend: str = "logfire",
                     base_url: str = None,
                     write_token: str = None,
                     **kwargs):
    """Set the global log provider."""

    global _GLOBAL_LOG_PROVIDER

    if provider == "otlp":
        from aworld.logs.provider.otlp_log import OTLPLoggerProvider
        _GLOBAL_LOG_PROVIDER = OTLPLoggerProvider(backend=backend,
                                                  base_url=base_url,
                                                  write_token=write_token,
                                                  **kwargs)


def get_log_provider() -> LoggerProvider:
    """
    Get the global log provider.
    """
    global _GLOBAL_LOG_PROVIDER
    if _GLOBAL_LOG_PROVIDER is None:
        raise ValueError("No log provider has been set.")
    return _GLOBAL_LOG_PROVIDER
