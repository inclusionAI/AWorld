# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PostLLMCallHook, PreLLMCallHook
from aworld.utils.common import convert_to_snake
from aworld.utils.serialized_util import to_serializable


@HookFactory.register(name="PreLLMCallContextProcessHook",
                      desc="PreLLMCallContextProcessHook")
class PreLLMCallContextProcessHook(PreLLMCallHook):
    """Process in the hook point of the pre_llm_call."""
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("PreLLMCallContextProcessHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        # and do something
        pass


@HookFactory.register(name="PostLLMCallContextProcessHook",
                      desc="PostLLMCallContextProcessHook")
class PostLLMCallContextProcessHook(PostLLMCallHook):
    """Process in the hook point of the post_llm_call."""
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("PostLLMCallContextProcessHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        # get context
        pass


@HookFactory.register(name="PostLLMTrajectoryHook",
                      desc="PostLLMTrajectoryHook")
class PostLLMTrajectoryHook(PostLLMCallHook):
    """Update trajectory after llm call."""
    __metaclass__ = abc.ABCMeta
    def name(self):
        return convert_to_snake("PostLLMTrajectoryHook")
    async def exec(self, message: Message, context: Context = None) -> Message:
        # get context
        agent_message = message.headers.get("agent_message")
        if not agent_message:
            return None
        await context.append_trajectory_from_message(agent_message)
        # data_row = context.trajectory_dataset.message_to_datarow(agent_message)
        # if data_row:
        #     row_data = to_serializable(data_row)
        #     await context.update_task_trajectory(context.task_id, [row_data])
        # pass