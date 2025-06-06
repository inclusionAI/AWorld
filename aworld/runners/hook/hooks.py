# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from aworld.core.event.base import Message


class HookPoint:
    START = "start"
    FINISHED = "finished"
    ERROR = "error"


class Hook:
    """Runner hook."""
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def point(self):
        """Hook point."""

    @abc.abstractmethod
    async def exec(self, message: Message) -> Message:
        """Execute hook function."""


class StartHook(Hook):
    """Process in the hook point of the start."""
    __metaclass__ = abc.ABCMeta


class FinishedHook(Hook):
    """Process in the hook point of the finished."""
    __metaclass__ = abc.ABCMeta


class ErrorHook(Hook):
    """Process in the hook point of the error."""
    __metaclass__ = abc.ABCMeta
