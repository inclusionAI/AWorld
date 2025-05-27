# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Dict, Any, List, Callable

from aworld.core.context.base import Context
from aworld.core.event.event_bus import InMemoryEventbus
from aworld.core.event.base import Constants, Message


class EventManager:
    """The event manager is now used to build an event bus instance and store the messages recently."""

    def __init__(self, **kwargs):
        # use conf to build event bus instance
        self.event_bus = InMemoryEventbus()
        self.context = Context.instance()
        # Record events in memory for re-consume.
        self.messages: Dict[str, List[Message]] = {'None': []}
        self.max_len = kwargs.get('max_len', 1000)

    async def emit(
            self,
            data: Any,
            sender: str,
            receiver: str = None,
            topic: str = None,
            session_id: str = None,
            event_type: str = Constants.TASK
    ):
        """Send data to the event bus.

        Args:
            data: Message payload.
            sender: The sender name of the message.
            receiver: The receiver name of the message.
            topic: The topic to which the message belongs.
            session_id: Special session id.
            event_type: Event type.
        """
        event = Message(
            payload=data,
            session_id=session_id if session_id else self.context.session_id,
            sender=sender,
            receiver=receiver,
            topic=topic,
            category=event_type,
        )
        return await self.emit_message(event)

    async def emit_message(self, event: Message):
        """Send the message to the event bus."""
        key = event.key()
        if key not in self.messages:
            self.messages[key] = []
        self.messages[key].append(event)
        if len(self.messages) > self.max_len:
            self.messages = self.messages[-self.max_len:]

        await self.event_bus.publish(event)
        return True

    async def consume(self, nowait: bool = False):
        if nowait:
            return await self.event_bus.consume_nowait()
        return await self.event_bus.consume()

    async def register(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.subscribe(event_type, topic, handler, **kwargs)

    async def unregister(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.unsubscribe(event_type, topic, handler, **kwargs)

    async def register_transformer(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.subscribe(event_type, topic, handler, transformer=True, **kwargs)

    async def unregister_transformer(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.unsubscribe(event_type, topic, handler, transformer=True, **kwargs)

    def messages_by_key(self, key: str) -> List[Message]:
        return self.messages.get(key, [])

    def messages_by_sender(self, sender: str, key: str):
        results = []
        for res in self.messages.get(key, []):
            if res.sender == sender:
                results.append(res)
        return results

    def messages_by_topic(self, topic: str, key: str):
        results = []
        for res in self.messages.get(key, []):
            if res.topic == topic:
                results.append(res)
        return results

    def session_messages(self, session_id: str) -> List[Message]:
        return [m for k, msg in self.messages.items() for m in msg if m.session_id == session_id]

    @staticmethod
    def mark_valid(messages: List[Message]):
        for msg in messages:
            msg.is_valid = True

    @staticmethod
    def mark_invalid(messages: List[Message]):
        for msg in messages:
            msg.is_valid = False

    def clear_messages(self):
        self.messages = []
