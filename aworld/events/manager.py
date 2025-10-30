# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Dict, Any, List, Callable

from aworld.core.context.base import Context
from aworld.events import eventbus
from aworld.core.event.base import Constants, Message
from aworld.core.storage.data import Data
from aworld.core.storage.inmemory_store import InmemoryStorage, InmemoryConfig


class EventManager:
    """The event manager is now used to build an event bus instance and store the messages recently."""

    def __init__(self, context: Context, **kwargs):
        # use conf to build event bus instance
        self.event_bus = eventbus
        self.context = context
        # Record events in memory for re-consume.
        self.max_len = kwargs.get('max_len', 1000)
        self.store = InmemoryStorage(InmemoryConfig(max_capacity=self.max_len))

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
            headers={"context": self.context}
        )
        return await self.emit_message(event)

    async def emit_message(self, event: Message):
        """Send the message to the event bus."""
        await self.store.create_data(Data(block_id=event.context.get_task().id, value=event, id=event.id))
        await self.event_bus.publish(event)
        return True

    async def consume(self, nowait: bool = False):
        msg = Message(session_id=self.context.session_id, sender="", category="", payload="")
        msg.context = self.context
        if nowait:
            return await self.event_bus.consume_nowait(msg)
        return await self.event_bus.consume(msg)

    async def done(self):
        await self.event_bus.done(self.context.task_id)

    async def register(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.subscribe(self.context._task_id, event_type, topic, handler, **kwargs)

    async def unregister(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.unsubscribe(self.context._task_id, event_type, topic, handler, **kwargs)

    async def register_transformer(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.subscribe(self.context._task_id, event_type, topic, handler, transformer=True, **kwargs)

    async def unregister_transformer(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.unsubscribe(self.context._task_id, event_type, topic, handler, transformer=True, **kwargs)

    def get_handlers(self, event_type: str) -> Dict[str, List[Callable[..., Any]]]:
        return self.event_bus.get_handlers(self.context._task_id, event_type)

    def get_transform_handler(self, key: str) -> Callable[..., Any]:
        return self.event_bus.get_transform_handler(self.context.task_id, key)

    async def messages_by_key(self, key: str) -> List[Message]:
        # key is task_id
        results = await self.store.get_data_items(key)
        if not results:
            return []

        reses = []
        for res in results:
            reses.append(res.value)
        return reses

    async def messages_by_sender(self, sender: str, key: str):
        # key is task_id
        results = []
        reses = await self.messages_by_key(key)
        for res in reses:
            if res.sender == sender:
                results.append(res)
        return results

    async def messages_by_topic(self, topic: str, key: str):
        # key is task_id
        results = []
        reses = await self.messages_by_key(key)
        for res in reses:
            if res.topic == topic:
                results.append(res)
        return results

    async def messages_by_session_id(self, session_id: str) -> List[Message]:
        # select all data
        results = await self.store.select_data()
        return [m for m in results if m.session_id == session_id]

    async def messages_by_task_id(self, task_id: str):
        results = []
        reses = await self.messages_by_key(task_id)
        for msg in reses:
            if msg.context.task_id == task_id:
                results.append(msg)
        results.sort(key=lambda x: x.timestamp)
        return results
