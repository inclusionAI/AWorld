# coding: utf-8

import traceback

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.experimental.metalearning.knowledge.mind_stream import retrieve_traj_and_draw_mind_stream
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PostLLMCallHook
from aworld.utils.common import convert_to_snake


@HookFactory.register(name="MindStreamHook", desc="POST_LLM HOOK for drawing team dynamic graph structure")
class MindStreamHook(PostLLMCallHook):
    """POST_LLM HOOK for reading traj data and drawing graph structure HTML page"""

    def name(self):
        return convert_to_snake("MindStreamHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        try:
            logger.info("MindStreamHook: Starting execution")
            if context is None:
                context = message.context

            if context is None:
                logger.warning("MindStreamHook: context is None, skip execution")
                return message


            await retrieve_traj_and_draw_mind_stream(context)
        except Exception as e:
            logger.error(f"MindStreamHook execution failed: {str(e)}, traceback: {traceback.format_exc()}")

        return message
