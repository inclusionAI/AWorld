
# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from datetime import time

from aworld.core.agent.base import AgentFactory
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PostLLMCallHook, PreLLMCallHook, PostToolCallHook
from aworld.utils.common import convert_to_snake
from train.examples.train_gaia_with_aworld_verl.env.utils import mcp_screen_snapshot, parse_and_save_screenshots


@HookFactory.register(name="PostLLMCallRolloutHook",
                      desc="PostLLMCallRolloutHook")
class PostLLMCallRolloutHook(PostLLMCallHook):
    """Process in the hook point of the post_llm_call."""
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("PostLLMCallRolloutHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        llm_response = message.payload
        if llm_response.content and "为空" in llm_response.content and '快照' in llm_response.content:
            agent = AgentFactory.agent_instance(message.sender)
            logger.info(f"env playwright empty screen shot, agent: {agent.name} time: {time.isoformat()}")
            screen_shot_result = await mcp_screen_snapshot(agent, context)
            if screen_shot_result:
                task_id = context.task_id if context and context.task_id else None
                saved_files, all_empty = parse_and_save_screenshots(screen_shot_result, task_id=task_id)
                if all_empty:
                    logger.error(f"All content is empty, retrying mcp_screen_snapshot. agent: {agent.name}, task_id: {task_id}")
                    screen_shot_result = await mcp_screen_snapshot(agent, context)
                    if screen_shot_result:
                        parse_and_save_screenshots(screen_shot_result, task_id=task_id)

        pass

@HookFactory.register(name="PostToolCallRolloutHook",
                      desc="PostToolCallRolloutHook")
class PostToolCallRolloutHook(PostToolCallHook):
    """Process in the hook point of the post_llm_call."""
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("PostToolCallRolloutHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        agent = AgentFactory.agent_instance(message.sender)
        screen_shot_result = await mcp_screen_snapshot(agent, context)
        if screen_shot_result:
            task_id = context.task_id if context and context.task_id else None
            saved_files, all_empty = parse_and_save_screenshots(screen_shot_result, task_id=task_id)
            if all_empty:
                logger.error(f"All content is empty, retrying mcp_screen_snapshot. agent: {agent.name}, task_id: {task_id}")
                screen_shot_result = await mcp_screen_snapshot(agent, context)
                if screen_shot_result:
                    parse_and_save_screenshots(screen_shot_result, task_id=task_id)
        pass


