# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from typing import Callable, Any, Dict, List

from aworld.core.singleton import InheritanceSingleton

from aworld.core.common import Config
from aworld.core.event.base import Message, Messageable


class Eventbus(Messageable, InheritanceSingleton):
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf: Config = None, **kwargs):
        super().__init__(conf, **kwargs)
        # {task_id: {event_type: {topic: [handler1, handler2]}}}
        self._subscribers: Dict[str, Dict[str, Dict[str, List[Callable[..., Any]]]]] = {}

        # {task_id: {event_type, handler}}
        self._transformer: Dict[str, Dict[str, Callable[..., Any]]] = {}

    async def send(self, message: Message, **kwargs):
        return await self.publish(message, **kwargs)

    async def receive(self, message: Message, **kwargs):
        return await self.consume(message)

    async def publish(self, messages: Message, **kwargs):
        """Publish a message, equals `send`."""

    async def consume(self, message: Message, **kwargs):
        """Consume the message queue."""

    async def subscribe(self, event_type: str, topic: str, handler: Callable[..., Any], task_id: str, **kwargs):
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

    async def unsubscribe(self, event_type: str, topic: str, handler: Callable[..., Any], task_id: str, **kwargs):
        """Unsubscribe the handler to the event type and the topic.

        Args:
            event_type: Type of events, fixed ones(task, agent, tool, error).
            topic: Classify messages through the topic.
            handler: Function of handle the event type and topic message.
            kwargs:
                - transformer: Whether it is a transformer handler.
        """

    def get_handlers(self, task_id: str, event_type: str) -> Dict[str, List[Callable[..., Any]]]:
        return self._subscribers.get(task_id, {}).get(event_type, {})

    def get_topic_handlers(self, task_id: str, event_type: str, topic: str) -> List[Callable[..., Any]]:
        return self._subscribers.get(task_id, {}).get(event_type, {}).get(topic, [])

    def get_transform_handler(self, task_id: str, key: str) -> Callable[..., Any]:
        return self._transformer.get(task_id, {}).get(key, None)

    def close(self):
        pass
