# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from typing import Callable, Any

from aworld.core.context.base import Context
from aworld.core.event import eventbus
from aworld.core.event.base import Message, Constants
from aworld.events.manager import EventManager
from aworld.utils.common import sync_exec


def subscribe(key: str, category: str = None):
    """Subscribe the special event to handle.

    Examples:
        >>> cate = Constants.TOOL or Constants.AGENT; key = "topic"
        >>> @subscribe(category=cate, key=key)
        >>> def example(message: Message) -> Message | None:
        >>>     print("do something")

    Args:
         key: The index key of the handler.
         category: Types of subscription events, the value is `agent` or `tool`.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if category is None:
            sync_exec(eventbus.subscribe, Constants.TOOL, key, func)
            sync_exec(eventbus.subscribe, Constants.AGENT, key, func)
        else:
            sync_exec(eventbus.subscribe, category, key, func)
        return func

    return decorator

async def _send_message(msg: Message) -> str:
    context = msg.context
    if not context:
        context = Context()

    event_mng = context.event_manager
    if not event_mng:
        event_mng = EventManager()

    await event_mng.emit_message(msg)
    return msg.id


async def send_message(msg: Message) -> asyncio.Task:
    """Utility function of send event.

    Args:
        msg: The content and meta information to be sent.
    """
    task = asyncio.create_task(_send_message(msg), name=msg.id)
    return task
