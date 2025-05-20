# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Dict, Any, List, Callable

from aworld.core.context.base import Context
from aworld.core.event.event_bus import InMemoryEventbus
from aworld.core.event.base import EventType, Message


class EventManager:
    """The event manager is now used to build an event bus instance and store the messages recently."""

    def __init__(self, **kwargs):
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
            event_type: str = EventType.TASK
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
        topic = event.topic
        receiver = event.receiver
        key = f'{event.category}_{topic if topic else receiver if receiver else ''}'
        if key not in self.messages:
            self.messages[key] = []
        self.messages[key].append(event)
        if len(self.messages) > self.max_len:
            self.messages = self.messages[-self.max_len:]

        await self.event_bus.publish(event)
        return True

    async def consume(self):
        return await self.event_bus.consume()

    async def register(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.subscribe(event_type, topic, handler, **kwargs)

    async def unregister(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.unsubscribe(event_type, topic, handler, **kwargs)

    async def register_transformer(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.subscribe(event_type, topic, handler, transformer=True, **kwargs)

    async def unregister_transformer(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.unsubscribe(event_type, topic, handler, transformer=True, **kwargs)

    def topic_messages(self, topic: str) -> List[Message]:
        return self.messages.get(topic, [])

    def session_messages(self, session_id: str) -> List[Message]:
        return [m for k, msg in self.messages.items() for m in msg if m.session_id == session_id]

    def messages_by_name(self, name: str, topic: str):
        results = []
        for res in self.messages.get(topic, []):
            if res.sender == name:
                results.append(res)
        return results

    def clear_messages(self):
        self.messages = []
