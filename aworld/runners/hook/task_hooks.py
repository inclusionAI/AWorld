# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PostLLMCallHook, PreLLMCallHook, Hook
from aworld.utils.common import convert_to_snake


@HookFactory.register(name="OnRunTaskProcessHook",
                      desc="OnRunTaskProcessHook")
class OnRunTaskProcessHook(Hook):
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("OnRunTaskProcessHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        # get context
        pass


@HookFactory.register(name="OnSuccessTaskProcessHook",
                      desc="OnSuccessTaskProcessHook")
class OnSuccessTaskProcessHook(Hook):
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("OnSuccessTaskProcessHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        # get context
        pass


@HookFactory.register(name="OnErrorTaskProcessHook",
                      desc="OnErrorTaskProcessHook")
class OnErrorTaskProcessHook(Hook):
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("OnErrorTaskProcessHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        # get context
        pass


@HookFactory.register(name="OnFinishTaskProcessHook",
                      desc="OnFinishTaskProcessHook")
class OnFinishTaskProcessHook(Hook):
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("OnFinishTaskProcessHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        # get context
        pass