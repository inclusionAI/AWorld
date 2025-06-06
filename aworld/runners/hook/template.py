# coding: utf-8
# Copyright (c) 2025 inclusionAI.

HOOK_TEMPLATE = """
import traceback

from aworld.core.context.base import Context

from aworld.core.event.base import Message, Constants
from aworld.runners.hook.hooks import *
from aworld.runners.hook.hook_factory import HookFactory
from aworld.logs.util import logger
from aworld.runners.utils import TaskType
from aworld.utils.common import convert_to_snake


@HookFactory.register(name="{name}",
                      desc="{desc}")
class {name}({point}Hook):
    def name(self):
        return convert_to_snake("{name}")
    
    def point(self):
        return convert_to_snake("{point}")

    async def exec(self, message: Message) -> Message:
        {func_import}import {func}
        try:
            res = {func}(message)
            if not res:
                raise ValueError(f"{func} no result return.")
            return Message(payload=res,
                           session_id=Context.instance().session_id,
                           sender="{name}",
                           category=Constants.TASK,
                           topic="{topic}")
        except Exception as e:
            logger.error(traceback.format_exc())
            return Message(payload=str(e),
                           session_id=Context.instance().session_id,
                           sender="{name}",
                           category=Constants.TASK,
                           topic=TaskType.ERROR)
"""
