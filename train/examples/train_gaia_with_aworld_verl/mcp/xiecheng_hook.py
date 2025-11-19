
# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from aworld.core.agent.base import AgentFactory
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PostLLMCallHook
from aworld.utils.common import convert_to_snake


@HookFactory.register(name="PostLLMCallRolloutHook",
                      desc="PostLLMCallRolloutHook")
class PostLLMCallRolloutHook(PostLLMCallHook):
    """Process in the hook point of the post_llm_call."""
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("PostLLMCallRolloutHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        agent = AgentFactory.agent_instance(message.sender)
        sand_box = agent.sandbox

        # 第一次 MCP 调用：browser_navigate
        try:
            await sand_box.mcpservers.call_tool(
                action_list=[
                    {
                        "tool_name": "cursor-browser-extension",
                        "action_name": "browser_navigate",
                        "params": {
                            "url": "https://www.ctrip.com"
                        }
                    }
                ],
                task_id=context.task_id if context else None,
                session_id=context.session_id if context else None,
                context=context
            )
            logger.info("browser_navigate to https://www.ctrip.com completed")
        except Exception as e:
            logger.error(f"browser_navigate failed: {e}")

        # 第二次 MCP 调用：browser_evaluate
        try:
            await sand_box.mcpservers.call_tool(
                action_list=[
                    {
                        "tool_name": "cursor-browser-extension",
                        "action_name": "browser_evaluate",
                        "params": {
                            "function": "()=>{document.cookie=\"ck=8E90025ED3BF7983437DB7E1BEFC5A31437CB87D60223839AFA0817D08246D43; path=/\";console.log('Write cookie success!')}"
                        }
                    }
                ],
                task_id=context.task_id if context else None,
                session_id=context.session_id if context else None,
                context=context
            )
            logger.info("browser_evaluate set cookie completed")
        except Exception as e:
            logger.error(f"browser_evaluate failed: {e}")

        return message
