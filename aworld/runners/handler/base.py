# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from typing import TypeVar, Generic, AsyncGenerator

from aworld.core.event.base import Message

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
