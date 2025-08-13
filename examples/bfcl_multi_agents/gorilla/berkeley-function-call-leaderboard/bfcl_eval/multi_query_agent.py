import sys, os
import time
from datetime import datetime

__root_path__ = os.path.dirname(os.path.abspath(__file__))
for _ in range(5):
    __root_path__ = os.path.dirname(__root_path__)
sys.path.append(__root_path__)

from typing import Dict, Any, List, Union, Callable

from aworld.agents.llm_agent import Agent
from aworld.core.common import ActionResult, Observation, ActionModel, Config
from aworld.core.event.base import Message, ToolMessage, Constants, AgentMessage, GroupMessage, TopicType
from aworld.memory.models import MessageMetadata, MemoryAIMessage, MemoryToolMessage, MemoryHumanMessage, \
    MemorySystemMessage, MemoryMessage
from aworld.core.context.base import Context
from aworld.logs.util import logger, color_log, Color

class MultiQueryAgent(Agent):
    def __init__(self, **kwargs):
        super(MultiQueryAgent, self).__init__(**kwargs)

    async def _add_system_message_to_memory(self, context: Context, content: str):
        if not self.system_prompt:
            return
        session_id = context.get_task().session_id
        task_id = context.get_task().id
        user_id = context.get_task().user_id

        histories = self.memory.get_last_n(0, filters={
            "agent_id": self.id(),
            "session_id": session_id,
        }, agent_memory_config=self.memory_config)
        if histories and len(histories) > 0:
            logger.debug(
                f"🧠 [MEMORY:short-term] histories is not empty, do not need add system input to agent memory")
            return
        if not self.system_prompt:
            return
        content = await self.custom_system_prompt(context=context, content=content, tool_list=self.tools)
        logger.info(f'system prompt content: {content}')

        await self.memory.add(MemorySystemMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=self.id(),
                agent_name=self.name(),
            )
        ), agent_memory_config=self.memory_config)
        logger.info(
            f"🧠 [MEMORY:short-term] Added system input to agent memory:  Agent#{self.id()}, 💬 {content[:100]}...")

    async def async_messages_transform(self,
                                       image_urls: List[str] = None,
                                       observation: Observation = None,
                                       message: Message = None,
                                       **kwargs):
        """Transform the original content to LLM messages of native format.

        Args:
            content: User content.
            image_urls: List of images encoded using base64.
            sys_prompt: Agent system prompt.
            max_step: The maximum list length obtained from memory.
        Returns:
            Message list for LLM.
        """
        agent_prompt = self.agent_prompt
        messages = []
        # append sys_prompt to memory
        await self._add_system_message_to_memory(context=message.context, content=observation.content)

        session_id = message.context.get_task().session_id
        task_id = message.context.get_task().id
        histories = self.memory.get_all(filters={
            "agent_id": self.id(),
            "session_id": session_id,
            "task_id": task_id,
            "memory_type": "message"
        })
        last_history = histories[-1] if histories and len(histories) > 0 else None

        # append observation to memory
        if observation.is_tool_result:
            for action_item in observation.action_result:
                tool_call_id = action_item.tool_call_id
                await self._add_tool_result_to_memory(tool_call_id, tool_result=action_item, context=message.context)
        elif not self.use_tools_in_prompt and last_history and last_history.metadata and "tool_calls" in last_history.metadata and \
                last_history.metadata[
                    'tool_calls']:
            for tool_call in last_history.metadata['tool_calls']:
                tool_call_id = tool_call['id']
                tool_name = tool_call['function']['name']
                if tool_name and tool_name == message.sender:
                    await self._add_tool_result_to_memory(tool_call_id, tool_result=observation.content,
                                                          context=message.context)
                    break
        else:
            content = observation.content
            logger.debug(f"agent_prompt: {agent_prompt}")
            if agent_prompt:
                content = agent_prompt.format(task=content, current_date=datetime.now().strftime("%Y-%m-%d"))
            if image_urls:
                urls = [{'type': 'text', 'text': content}]
                for image_url in image_urls:
                    urls.append(
                        {'type': 'image_url', 'image_url': {"url": image_url}})
                content = urls
            await self._add_human_input_to_memory(content, message.context, memory_type="message")

        # from memory get last n messages
        histories = self.memory.get_last_n(self.memory_config.history_number, filters={
            "agent_id": self.id(),
            "session_id": session_id,
            # "task_id": task_id
        }, agent_memory_config=self.memory_config)
        if histories:
            # default use the first tool call
            for history in histories:
                if isinstance(history, MemoryMessage):
                    messages.append(history.to_openai_message())
                else:
                    if not self.use_tools_in_prompt and "tool_calls" in history.metadata and history.metadata[
                        'tool_calls']:
                        messages.append({'role': history.metadata['role'], 'content': history.content,
                                         'tool_calls': [history.metadata["tool_calls"][0]]})
                    else:
                        messages.append({'role': history.metadata['role'], 'content': history.content,
                                         "tool_call_id": history.metadata.get("tool_call_id")})

        # truncate and other process
        try:
            messages = self._process_messages(messages=messages, context=self.context)
            # print(messages[1:])
        except Exception as e:
            logger.warning(f"Failed to process messages in messages_transform: {e}")
            logger.debug(f"Process messages error details: {traceback.format_exc()}")
        return messages


if __name__ == '__main__':
    ...