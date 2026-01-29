"""Decorators to simplify subscriber handler registration."""

import asyncio
import logging
import threading
from collections import defaultdict
from typing import Callable, Dict, Optional

from env_channel.common.filter import MessageFilter
from env_channel.common.message import EnvChannelMessage

# Global registry for decorated handlers
_HANDLER_REGISTRY: list[dict] = []
# Background runner threads per server_url
_RUNNER_THREADS: dict[str, threading.Thread] = {}

logger = logging.getLogger(__name__)


def env_channel_sub(
    *,
    server_url: str,
    topics: Optional[list[str]] = None,
    filter: Optional[MessageFilter] = None,
    reconnect_interval: float = 10.0,
    auto_reconnect: bool = True,
    auto_connect: bool = True,
    auto_start: bool = True,
    headers: Optional[Dict[str, str]] = None,
) -> Callable:
    """
    Decorator to mark a handler function with topics/filter for subscription.

    Usage:
        Basic usage (with default reconnect parameters)::

            @env_channel_sub(server_url="ws://localhost:8765", topics=["demo-channel"])
            async def handle(msg: EnvChannelMessage):
                ...

        Custom auto-reconnect parameters::

            @env_channel_sub(
                server_url="ws://localhost:8765",
                topics=["demo-channel"],
                auto_connect=True,
                auto_reconnect=True,
                reconnect_interval=5.0,
            )
            async def handle(msg: EnvChannelMessage):
                ...
    """

    def decorator(func: Callable):
        setattr(func, "__envchannel_topics", topics or ["default"])
        setattr(func, "__envchannel_filter", filter)
        setattr(func, "__envchannel_server_url", server_url)
        setattr(func, "__envchannel_auto_connect", auto_connect)
        setattr(func, "__envchannel_auto_reconnect", auto_reconnect)
        setattr(func, "__envchannel_reconnect_interval", reconnect_interval)
        setattr(func, "__envchannel_headers", headers)

        async def wrapper(message: EnvChannelMessage):
            if asyncio.iscoroutinefunction(func):
                return await func(message)
            return func(message)

        # register
        _HANDLER_REGISTRY.append(
            {
                "handler": wrapper,
                "topics": topics or ["default"],
                "filter": filter,
                "server_url": server_url,
                "auto_connect": auto_connect,
                "auto_reconnect": auto_reconnect,
                "reconnect_interval": reconnect_interval,
                "headers": headers,
            }
        )

        if auto_start:
            _ensure_runner(server_url)

        return wrapper

    return decorator


def get_registered_handlers_grouped():
    """
    Group registered handlers by server_url.
    Returns: dict[server_url, list[ (handler, topics, filter) ]]
    """
    grouped = defaultdict(list)
    for item in _HANDLER_REGISTRY:
        grouped[item["server_url"]].append(
            (item["handler"], item["topics"], item["filter"])
        )
    return grouped


def _ensure_runner(server_url: str) -> None:
    if server_url in _RUNNER_THREADS:
        return
    t = threading.Thread(target=_runner_thread, args=(server_url,), daemon=True)
    _RUNNER_THREADS[server_url] = t
    t.start()


def _runner_thread(server_url: str) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_runner_coroutine(server_url))
    finally:
        loop.stop()
        loop.close()


async def _runner_coroutine(server_url: str) -> None:
    """Background coroutine to run subscribers for a given server_url.

    Behavior:
    - If the initial connection fails (e.g., Server not started yet), it will retry
      every reconnect_interval seconds until successful.
    - After success, EnvChannelSubscriber's internal auto_reconnect handles
      reconnection on disconnection.
    """
    # Lazy import to avoid circular dependency
    from env_channel.client.subscriber import EnvChannelSubscriber
    from env_channel.common.exceptions import ConnectionError

    # Aggregate connection config for this server_url from registered handlers
    # (explicit config takes priority if available)
    auto_connect = True
    auto_reconnect = True
    reconnect_interval = 10.0
    headers: Optional[Dict[str, str]] = None

    for item in _HANDLER_REGISTRY:
        if item["server_url"] != server_url:
            continue
        if "auto_connect" in item and item["auto_connect"] is not None:
            auto_connect = item["auto_connect"]
        if "auto_reconnect" in item and item["auto_reconnect"] is not None:
            auto_reconnect = item["auto_reconnect"]
        if "reconnect_interval" in item and item["reconnect_interval"] is not None:
            reconnect_interval = item["reconnect_interval"]
        if "headers" in item and item["headers"] is not None:
            # If multiple headers are configured for the same server_url,
            # use "last one wins" strategy
            headers = item["headers"]

    sub = EnvChannelSubscriber(
        server_url=server_url,
        auto_connect=auto_connect,
        auto_reconnect=auto_reconnect,
        reconnect_interval=reconnect_interval,
        headers=headers,
    )

    try:
        attempt = 0
        while True:
            try:
                attempt += 1
                await sub.subscribe_registered_handlers(server_url=server_url)
                logger.info(
                    "Decorator runner subscribed handlers for %s (after %d attempt(s))",
                    server_url,
                    attempt,
                )
                # Keep running until cancelled; reconnection on disconnect
                # is handled by EnvChannelSubscriber internally
                while True:
                    await asyncio.sleep(1)
            except ConnectionError as e:
                level = logging.ERROR if attempt == 1 else logging.WARNING
                logger.log(
                    level,
                    "Decorator runner connect failed for %s (attempt %d): %s; "
                    "will retry in %.1fs",
                    server_url,
                    attempt,
                    e,
                    reconnect_interval,
                )
                await asyncio.sleep(reconnect_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Decorator runner unexpected error for %s (attempt %d): %s; "
                    "will retry in %.1fs",
                    server_url,
                    attempt,
                    e,
                    reconnect_interval,
                )
                await asyncio.sleep(reconnect_interval)
    finally:
        await sub.disconnect()

