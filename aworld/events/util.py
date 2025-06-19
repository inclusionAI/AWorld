# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.events.manager import EventManager


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
    task = asyncio.create_task(send_message(msg), name=msg.id)
    return task
