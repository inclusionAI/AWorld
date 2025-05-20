# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import json
import socket
import struct
import threading
import time
import uuid
from asyncio import iscoroutinefunction, Queue
from inspect import isfunction
from typing import Callable, Any, Dict, List

from aworld.core.singleton import InheritanceSingleton

from aworld.core.common import Config
from aworld.core.event.base import Message, Messageable
from aworld.logs.util import logger


class Eventbus(Messageable, InheritanceSingleton):
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf: Config = None, **kwargs):
        super().__init__(conf, **kwargs)
        # {event_type: {topic: [handler1, handler2]}}
        self._subscribers: Dict[str, Dict[str, List[Callable[..., Any]]]] = {}

        # {event_type, handler}
        self._transformer: Dict[str, Callable[..., Any]] = {}

    async def send(self, message: Message, **kwargs):
        return await self.publish(message, **kwargs)

    async def receive(self, message: Message, **kwargs):
        return await self.consume()

    async def publish(self, messages: Message, **kwargs):
        """Publish a message, equals `send`."""

    async def consume(self):
        """Consume the message queue."""

    async def subscribe(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        """Subscribe the handler to the event type and the topic.

        NOTE: The handler list is executed sequentially in the topic, the output
              of the previous one is the input of the next one.

        Args:
            event_type: Type of events, fixed ones(task, agent, tool, error).
            topic: Classify messages through the topic.
            handler: Function of handle the event type and topic message.
            kwargs:
                - transformer: Whether it is a transformer handler.
                - order: Handler order in the topic.
        """

    async def unsubscribe(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        """Unsubscribe the handler to the event type and the topic.

        Args:
            event_type: Type of events, fixed ones(task, agent, tool, error).
            topic: Classify messages through the topic.
            handler: Function of handle the event type and topic message.
            kwargs:
                - transformer: Whether it is a transformer handler.
        """

    def get_handlers(self, event_type: str) -> Dict[str, List[Callable[..., Any]]]:
        return self._subscribers.get(event_type, {})

    def get_topic_handlers(self, event_type: str, topic: str) -> List[Callable[..., Any]]:
        return self._subscribers.get(event_type, {}).get(topic, [])

    def get_transform_handlers(self, key: str) -> Callable[..., Any]:
        return self._transformer.get(key, None)

    def close(self):
        pass


class InMemoryEventbus(Eventbus):
    def __init__(self, conf: Config = None, **kwargs):
        super().__init__(conf, **kwargs)

        # use asyncio Queue as default
        self._message_queue: Queue = Queue()

    def wait_consume_size(self) -> int:
        return self._message_queue.qsize()

    async def publish(self, message: Message, **kwargs):
        await self._message_queue.put(message)

    async def consume(self):
        return await self._message_queue.get()

    async def consume_nowait(self):
        return await self._message_queue.get_nowait()

    async def subscribe(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        if kwargs.get("transformer"):
            if event_type in self._transformer:
                logger.warning(f"{event_type} transform already subscribe.")
                return

            if isfunction(handler):
                self._transformer[event_type] = handler
            else:
                logger.warning(f"{event_type} {topic} subscribe fail, handler {handler} is not a function.")
            return

        order = kwargs.get('order', 99999)

        handlers = self._subscribers.get(event_type)
        if not handlers:
            self._subscribers[event_type] = {}
        topic_handlers = self._subscribers[event_type].get(topic)
        if not topic_handlers:
            self._subscribers[event_type][topic] = []

        if order >= len(self._subscribers[event_type][topic]):
            self._subscribers[event_type][topic].append(handler)
        else:
            self._subscribers[event_type][topic].insert(order, handler)
        logger.info(f"subscribe {event_type} {topic} {handler} success.")
        logger.info(f"{self._subscribers}")

    async def unsubscribe(self, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        if kwargs.get("transformer"):
            if event_type not in self._transformer:
                logger.warning(f"{event_type} transform not subscribe.")
                return

            self._transformer.pop(event_type, None)
            return

        if event_type not in self._subscribers:
            logger.warning(f"{event_type} handler not register.")
            return

        handlers = self._subscribers[event_type]
        topic_handlers: List = handlers.get(topic, [])
        topic_handlers.remove(handler)
