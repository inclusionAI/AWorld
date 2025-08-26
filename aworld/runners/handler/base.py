# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from typing import TypeVar, Generic, AsyncGenerator

from aworld.core.event.base import Message, Constants
from aworld.logs.util import logger

IN = TypeVar('IN')
OUT = TypeVar('OUT')


class Handler(Generic[IN, OUT]):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    async def handle(self, data: IN) -> AsyncGenerator[OUT, None]:
        """Process the data as the expected result.

        Args:
            data: Data generated while running the task.
        """

    @classmethod
    def name(cls):
        """Handler name."""
        return cls.__name__


class DefaultHandler(Handler[Message, AsyncGenerator[Message, None]]):
    """Default handler."""

    def __init__(self):
        self.hooks = None

    def get_registered_name(self):
        """Get the registered name of the handler.
        
        If the class has a REGISTERED_NAME attribute, return the value of the attribute;
        otherwise return None.
        """
        return getattr(self.__class__, "REGISTERED_NAME", None)

    def is_valid_message(self, message: Message):
        """Validate if the message is valid for this handler.
        
        If the class has a REGISTERED_NAME attribute, check if the message's category matches the registered name;
        otherwise return True.
        """
        registered_name = self.get_registered_name()
        if registered_name is not None:
            return message.category == registered_name
        return True

    async def handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if not self.is_valid_message(message):
            return
        async for event in self._do_handle(message):
            msg = await self.post_handle(input=message, output=event)
            if msg:
                yield msg

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        yield message

    async def post_handle(self, input:Message, output: Message) -> Message:
        """Post handle the message.
        Args:
            message: Message generated while running the task.
        """
        return output

    async def run_hooks(self, message: Message, hook_point: str) -> AsyncGenerator[Message, None]:
        if not self.hooks:
            return
        hooks = self.hooks.get(hook_point, [])
        for hook in hooks:
            try:
                msg = await hook.exec(message)
                if msg:
                    yield msg
            except:
                logger.warning(f"{self.name()}|{hook.point()} {hook.name()} execute fail.")
