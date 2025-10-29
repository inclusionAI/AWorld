# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from asyncio import Queue, PriorityQueue, QueueEmpty
from inspect import isfunction
from typing import Dict, Callable, Any, List

from aworld.core.common import Config
from aworld.core.event.base import Message
from aworld.core.event.event_bus import Eventbus
from aworld.logs.util import logger


class InMemoryEventbus(Eventbus):
    def __init__(self, conf: Config = None, **kwargs):
        super().__init__(conf, **kwargs)

        # use asyncio Queue as default
        # use asyncio Queue as default, isolation based on session_id
        # self._message_queue: Queue = Queue()
        self._message_queue: Dict[str, Queue] = {}

    async def wait_consume_size(self, id: str) -> int:
        return self._message_queue.get(id, Queue()).qsize()

    async def publish(self, message: Message, **kwargs):
        logger.info(f"publish message: {message} of task: {message.task_id}")
        queue = self._message_queue.get(message.task_id)
        if not queue:
            queue = PriorityQueue()
            self._message_queue[message.task_id] = queue
        logger.debug(f"publish message: {message.task_id}:  {message.session_id}, queue: {id(queue)}")
        await queue.put(message)

    async def consume(self, message: Message, **kwargs):
        return await self._message_queue.get(message.task_id, PriorityQueue()).get()

    async def consume_nowait(self, message: Message):
        return self._message_queue.get(message.task_id, PriorityQueue()).get_nowait()

    async def done(self, id: str):
        # Only operate on an existing queue; avoid creating a new temporary queue via dict.get default
        queue = self._message_queue.get(id)
        if not queue:
            return
        # Drain all remaining items and mark them done to balance unfinished_tasks
        while True:
            try:
                queue.get_nowait()
                queue.task_done()
            except QueueEmpty:
                break

    async def subscribe(self, task_id: str, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        if kwargs.get("transformer"):
            # Initialize task_id dict if not exists
            if task_id not in self._transformer:
                self._transformer[task_id] = {}

            if event_type in self._transformer[task_id]:
                logger.warning(f"{event_type} transform already subscribe for task {task_id}.")
                return

            if isfunction(handler):
                self._transformer[task_id][event_type] = handler
            else:
                logger.warning(f"{event_type} {topic} subscribe fail, handler {handler} is not a function.")
            return

        order = kwargs.get('order', 99999)
        task_handlers = self._subscribers.get(task_id)
        if not task_handlers:
            task_handlers = {}
            self._subscribers[task_id] = task_handlers

        handlers = task_handlers.get(event_type)
        if not handlers:
            task_handlers[event_type] = {}
        topic_handlers = task_handlers[event_type].get(topic)
        if not topic_handlers:
            task_handlers[event_type][topic] = []

        if order >= len(self._subscribers[task_id][event_type][topic]):
            self._subscribers[task_id][event_type][topic].append(handler)
        else:
            self._subscribers[task_id][event_type][topic].insert(order, handler)
        logger.debug(f"subscribe {event_type} {topic} {handler} success.")
        logger.debug(f"subscribers {task_id}: {self._subscribers}")

    async def unsubscribe(self, task_id: str, event_type: str, topic: str, handler: Callable[..., Any], **kwargs):
        if kwargs.get("transformer"):
            if task_id not in self._transformer:
                logger.warning(f"{task_id} transform not subscribe.")
                return

            self._transformer[task_id].pop(event_type, None)
            return

        if task_id not in self._subscribers:
            logger.warning(f"{task_id} handler not register.")
            return

        if event_type not in self._subscribers[task_id]:
            logger.warning(f"{event_type} handler not register.")
            return

        handlers = self._subscribers[task_id][event_type]
        topic_handlers: List = handlers.get(topic, [])
        topic_handlers.remove(handler)
