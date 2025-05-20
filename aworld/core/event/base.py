# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict

from pydantic import BaseModel

from aworld.config import ConfigDict
from aworld.core.common import Config


class EventType:
    """The types of events are respectively oriented towards agents, tools, and applications.

    As the three types of entities are independent of each other within the framework, they can
    interact with third-party entities independently.

    For example, `agent` event types can interact with other agents through the A2A protocol,
    `tool` event types can interact with other tools through the MCP protocol,
    and `task` event types can interact with third-party applications.
    """
    AGENT = "agent"
    TOOL = "tool"
    TASK = "task"


@dataclass
class Message:
    """The message structure for event transmission.

    Each message has a unique ID, and the actual data is carried through the `payload` attribute,
    peer to peer(p2p) message transmission is achieved by setting the `receiver`, and topic based
    message transmission is achieved by setting the `topic`.

    Specific message recognition and processing can be achieved through the type of `payload`
    or by extending `Message`.
    """
    session_id: str
    payload: Any
    sender: str
    receiver: str = None
    caller: str = None
    id: str = uuid.uuid4().hex
    timestamp: int = time.time()
    # Topic of message
    topic: str = None
    # Type(event) of message
    category: str = EventType.TASK
    headers: Dict[str, Any] = field(default_factory=dict)
    group_name: str = None

    def key(self):
        category = self.category if self.category else ''
        if self.topic:
            return f'{category}_{self.topic}'
        else:
            return f'{category}_{self.receiver if self.receiver else ''}'


class Messageable(object):
    """Top-level API for data reception, transmission and transformation."""
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf: Config = None, **kwargs):
        self.conf = conf
        if isinstance(conf, Dict):
            self.conf = ConfigDict(conf)
        elif isinstance(conf, BaseModel):
            # To add flexibility
            self.conf = ConfigDict(conf.model_dump())

    @abc.abstractmethod
    async def send(self, message: Message, **kwargs):
        """Send a message to the receiver.

        Args:
            message: Message structure that carries the data that needs to be processed.
        """

    @abc.abstractmethod
    async def receive(self, message: Message, **kwargs):
        """Receive a message from the sender.

        Mainly used for request-driven (call), event-driven is generally handled using `Eventbus`.

        Args:
            message: Message structure that carries the data that needs to be processed.
        """

    async def transform(self, message: Message, **kwargs):
        """Transforms a message into a standardized format  from the sender.

        Args:
            message: Message structure that carries the data that needs to be processed.
        """


class Recordable(Messageable):
    """Top-level API for recording data."""

    async def send(self, message: Message, **kwargs):
        return await self.write(message, **kwargs)

    async def receive(self, message: Message, **kwargs):
        return await self.read(message, **kwargs)

    @abc.abstractmethod
    async def read(self, message: Message, **kwargs):
        """Read a message from the store.

        Args:
            message: Message structure that carries the data that needs to be read.
        """

    @abc.abstractmethod
    async def write(self, message: Message, **kwargs):
        """Write a message to the store.

        Args:
            message: Message structure that carries the data that needs to be write.
        """
