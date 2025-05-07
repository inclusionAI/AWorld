# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import time
import traceback
import uuid
from asyncio import iscoroutinefunction, Queue
from dataclasses import dataclass
from enum import Enum
from typing import List, Any, Dict, Callable

from pydantic import BaseModel

from aworld.config import ConfigDict
from aworld.core.singleton import InheritanceSingleton

from aworld.core.common import Config
from aworld.core.context.base import Context
from aworld.logs.util import logger


class EventType(Enum):
    AGENT = "agent"
    TOOL = "tool"
    TASK = "task"
    SESSION = "session"
    ERROR = "error"


@dataclass
class Message:
    session_id: str
    payload: Any
    sender: str
    receiver: str = None
    id: str = uuid.uuid4().hex
    timestamp: int = time.time()
    topic: str = None
    category: EventType = EventType.TASK

    def key(self):
        if self.topic:
            return self.category.value + self.topic
        else:
            return self.category.value + (self.receiver if self.receiver else '')


MessageType = Message | List[Message]


class Messageable(object):
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf: Config = None, **kwargs):
        self.conf = conf
        if isinstance(conf, Dict):
            self.conf = ConfigDict(conf)
        elif isinstance(conf, BaseModel):
            # To add flexibility
            self.conf = ConfigDict(conf.model_dump())

    @abc.abstractmethod
    async def send(self, messages: MessageType, **kwargs):
        """Send a message or message list to the receiver."""

    @abc.abstractmethod
    async def receive(self, messages: MessageType, **kwargs):
        """Receive a message or message list from the sender."""

    async def transform(self, messages: MessageType, **kwargs):
        """Transform a message or message list from the sender."""


class Recordable(Messageable):
    async def send(self, messages: MessageType, **kwargs):
        return self.write(messages, **kwargs)

    async def receive(self, messages: MessageType, **kwargs):
        return self.read(messages, **kwargs)

    @abc.abstractmethod
    async def read(self, messages: MessageType, **kwargs):
        """Read a message or message list from the store."""

    @abc.abstractmethod
    async def write(self, messages: MessageType, **kwargs):
        """Write a message or message list to the store."""


class EventBus(Messageable, InheritanceSingleton):
    def __init__(self, conf: Config = None, **kwargs):
        super().__init__(conf, **kwargs)
        self._subscribers: Dict[str, List[Callable[..., Any]]] = {}
        self._async_subscribers: Dict[str, List[Callable[..., Any]]] = {}

        self._transformer: Dict[str, Callable[..., Any]] = {}
        self._async_transformer: Dict[str, Callable[..., Any]] = {}

        # use asyncio Queue as default
        self._message_queues: Dict[str, Queue] = {}
        if conf and conf.get('auto_consume'):
            self._task = asyncio.create_task(self._run())
            self._stopped = asyncio.Event()

    async def _run(self):
        # auto consume message
        while True:
            if self._stopped.is_set():
                logger.info("stop consume...")
                return

            for k, queue in self._message_queues.items():
                try:
                    message = await queue.get()
                except Exception:
                    logger.warning(f"{k} queue already closed. {traceback.format_exc()}")
                    continue

                await self.subscribe(message)

    async def send(self, messages: MessageType, **kwargs):
        return self.publish(messages, **kwargs)

    async def receive(self, messages: MessageType, **kwargs):
        return self.subscribe(messages, **kwargs)

    async def publish(self, messages: MessageType, **kwargs):
        if isinstance(messages, Message):
            messages = [messages]

        for msg in messages:
            key = msg.key()
            if key not in self._message_queues:
                self._message_queues[key] = Queue()
            await self._message_queues[key].put(msg)

    async def subscribe(self, message: MessageType, **kwargs):
        if isinstance(message, Message):
            message = [message]

        for msg in message:
            key = msg.key()
            handlers = self._subscribers.get(key, [])
            async_handlers = self._async_subscribers.get(key, [])

            transformer = self._transformer.get(key, None)
            async_transformer = self._async_transformer.get(key, None)
            if transformer:
                msg = await self.transform(msg, handler=transformer)
            elif async_transformer:
                msg = await self.transform(msg, handler=async_transformer)

            for handler in handlers:
                if not handler:
                    logger.warning(f"{handler} is None")

                try:
                    handler(msg.payload)
                except Exception as e:
                    logger.warning(traceback.format_exc())
                    await self.publish(Message(
                        category=EventType.ERROR,
                        payload={"error": str(e), "message": msg},
                        sender='system',
                        session_id=Context.instance().session_id,
                        receiver=msg.receiver
                    ))

            results = []
            for handler in async_handlers:
                if not handler:
                    logger.warning(f"{handler} is None")

                results.append(handler(msg.payload))

            await asyncio.gather(*results)

    async def register(self, event_key: str, handler: Callable[..., Any], **kwargs):
        is_transform = kwargs.get("transformer")
        if is_transform:
            if event_key in self._subscribers or event_key in self._async_subscribers:
                logger.warning(f"{event_key} transform already register.")
                return

            if iscoroutinefunction(handler):
                self._transformer[event_key] = handler
            else:
                self._async_transformer[event_key] = handler
            return

        if event_key in self._subscribers or event_key in self._async_subscribers:
            logger.warning(f"{event_key} handler already register.")
            return

        if iscoroutinefunction(handler):
            self._async_subscribers[event_key] = []
            self._async_subscribers[event_key].append(handler)
        else:
            self._subscribers[event_key] = []
            self._subscribers[event_key].append(handler)

    async def unregister(self, event_key: str, handler: Callable[..., Any], **kwargs):
        is_transform = kwargs.get("transformer")
        if is_transform:
            self._transformer.pop(event_key, None)
            self._async_transformer.pop(event_key, None)
            return

        if event_key in self._subscribers:
            self._subscribers[event_key].remove(handler)
        if event_key in self._async_subscribers:
            self._async_subscribers[event_key].remove(handler)


class EventManager:
    """Record events in memory for re-consume."""

    def __init__(self, **kwargs):
        self.event_bus = EventBus({'auto_consume': True})
        self.context = Context.instance()
        self.messages: Dict[str, List[Message]] = {'None': []}
        self.max_len = kwargs.get('max_len', 1000)

    async def emit(
            self,
            data: Any,
            sender: str,
            receiver: str = None,
            topic: str = None,
            session_id: str = None,
            event_type: EventType = EventType.TASK
    ):
        """

        Args:
            data: Message payload.
            sender: The sender of the message.
            receiver: The receiver of the message.
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

        key = event_type.value + (topic if topic else receiver if receiver else '')
        if key not in self.messages:
            self.messages[key] = []
        self.messages[key].append(event)
        if len(self.messages) > self.max_len:
            self.messages = self.messages[-self.max_len:]

        await self.event_bus.publish(event)
        return True

    async def register(self, event_key: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.register(event_key, handler, **kwargs)

    async def unregister(self, event_key: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.unregister(event_key, handler, **kwargs)

    async def register_transformer(self, event_key: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.register(event_key, handler, transformer=True, **kwargs)

    async def unregister_transformer(self, event_key: str, handler: Callable[..., Any], **kwargs):
        await self.event_bus.unregister(event_key, handler, transformer=True, **kwargs)

    def topic_messages(self, topic: str) -> List[Message]:
        return self.messages.get(topic, [])

    def session_messages(self, session_id: str) -> List[Message]:
        return [m for k, msg in self.messages.items() for m in msg if m.session_id == session_id]

    def clear_messages(self):
        self.messages = []
