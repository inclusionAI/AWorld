# aworld/runners/handler/output.py
import json
from typing import AsyncGenerator, Any

from aworld.agents.llm_agent import Agent
from aworld.core.context.amni import AmniContext
from aworld.core.context.base import Context
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryToolMessage, MessageMetadata, MemoryHumanMessage, MemorySystemMessage, \
    MemoryAIMessage
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.core.common import TaskItem, ActionResult
from aworld.core.event.base import Message, Constants, TopicType, MemoryEventMessage, MemoryEventType
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory


@HandlerFactory.register(name=f'__{Constants.MEMORY}__')
class DefaultMemoryHandler(DefaultHandler):
    def __init__(self, runner):
        super().__init__(runner)
        self.runner = runner
        self.hooks = {}
        self.memory = MemoryFactory.instance()
        if runner.task.hooks:
            for k, vals in runner.task.hooks.items():
                self.hooks[k] = []
                for v in vals:
                    cls = HookFactory.get_class(v)
                    if cls:
                        self.hooks[k].append(cls)

    def is_valid_message(self, message: Message):
        if message.category != Constants.MEMORY:
            return False
        return True

    async def _do_handle(self, message: MemoryEventMessage):
        # Resolve agent from sender/receiver/headers
        context = message.context
        agent = message.agent

        if not agent:
            logger.warning("DefaultMemoryHandler: cannot resolve agent for memory event, skip.")
            return

        try:
            event_type = message.memory_event_type
            payload = message.payload

            if event_type == MemoryEventType.SYSTEM:
                # Accept raw content or dict with content
                content = None
                if isinstance(payload, dict):
                    content = payload.get("content")
                elif isinstance(payload, str):
                    content = payload
                if content:
                    await self._add_system_message_to_memory(agent, context, content)

            elif event_type == MemoryEventType.HUMAN:
                # Accept raw content or dict with content/memory_type
                memory_type = "init"
                content = payload
                if isinstance(payload, dict):
                    memory_type = payload.get("memory_type", "init")
                    content = payload.get("content", payload)
                await self.add_human_input_to_memory(agent, content, context, memory_type=memory_type)

            elif event_type == MemoryEventType.AI:
                # Accept ModelResponse or dict-compatible payload
                llm_response = payload
                history_messages = []
                if isinstance(payload, dict):
                    llm_response = payload.get("llm_response", payload)
                    history_messages = payload.get("history_messages", [])
                await self._add_llm_response_to_memory(agent, llm_response, context, history_messages)

            elif event_type == MemoryEventType.TOOL:
                # Accept ActionResult or dict with tool_call_id/tool_result/content
                tool_call_id = None
                tool_result = None
                if isinstance(payload, ActionResult):
                    tool_call_id = payload.tool_call_id
                    tool_result = payload
                elif isinstance(payload, dict):
                    tool_call_id = payload.get("tool_call_id")
                    inner_result = payload.get("tool_result")
                    if isinstance(inner_result, ActionResult):
                        tool_result = inner_result
                    else:
                        content = payload.get("content", payload)
                        tool_call_id = payload.get("tool_call_id", tool_call_id)
                        tool_result = ActionResult(content=content, tool_call_id=tool_call_id, success=True)
                if tool_call_id and tool_result:
                    await self.add_tool_result_to_memory(agent, tool_call_id, tool_result, context)
                else:
                    logger.warning("DefaultMemoryHandler: invalid TOOL payload, missing tool_call_id or tool_result.")
        except Exception:
            logger.warning("DefaultMemoryHandler: failed to write memory for event.", exc_info=True)

        # This handler only performs side-effects; do not emit framework messages
        if False:
            yield message
        return

    async def _add_system_message_to_memory(self, agent: Agent, context: Context, content: str):
        if not content:
            return

        if self._is_amni_context(context):
            logger.debug(f"memory is amni context, publish system prompt event")
            await context.pub_and_wait_system_prompt_event(
                system_prompt=content,
                user_query=context.task_input,
                agent_id=agent.id(),
                agent_name=agent.name(),
                namespace=agent.id())
            logger.info(f"_add_system_message_to_memory finish {agent.id()}")
            return
        session_id = context.get_task().session_id
        task_id = context.get_task().id
        user_id = context.get_task().user_id

        histories = self.memory.get_last_n(0, filters={
            "agent_id": agent.id(),
            "session_id": session_id,
            "task_id": task_id
        }, agent_memory_config=agent.memory_config)
        if histories:
            logger.debug(f"🧠 [MEMORY:short-term] histories is not empty, do not need add system input to agent memory")
            return

        await self.memory.add(MemorySystemMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=agent.id(),
                agent_name=agent.name(),
            )
        ), agent_memory_config=agent.memory_config)

    async def _add_llm_response_to_memory(self, agent: Agent, llm_response, context: Context, history_messages: list, **kwargs):
        """Add LLM response to memory"""
        ai_message = MemoryAIMessage(
            content=llm_response.content,
            tool_calls=llm_response.tool_calls,
            metadata=MessageMetadata(
                session_id=context.get_task().session_id,
                user_id=context.get_task().user_id,
                task_id=context.get_task().id,
                agent_id=agent.id(),
                agent_name=agent.name()
            )
        )
        agent_memory_config = agent.memory_config
        if self._is_amni_context(context):
            agent_memory_config = context.get_config().get_agent_context_config(agent.id())

        await self.memory.add(ai_message, agent_memory_config=agent_memory_config)

    async def add_human_input_to_memory(self, agent: Agent, content: Any, context: Context, memory_type="init"):
        """Add user input to memory"""
        session_id = context.get_task().session_id
        user_id = context.get_task().user_id
        task_id = context.get_task().id
        if not content:
            return

        agent_memory_config = agent.memory_config
        if self._is_amni_context(context):
            agent_memory_config = context.get_config().get_agent_context_config(agent.id())

        await self.memory.add(MemoryHumanMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=agent.id(),
                agent_name=agent.name(),
            ),
            memory_type=memory_type
        ), agent_memory_config=agent_memory_config)

    async def add_tool_result_to_memory(self, agent: 'Agent', tool_call_id: str, tool_result: ActionResult, context: Context):
        """Add tool result to memory"""
        if self._is_amni_context(context):
            logger.debug(f"memory is amni context, publish tool result prompt event")
            await context.pub_and_wait_tool_result_event(tool_result,
                                                         tool_call_id,
                                                         agent_id=agent.id(),
                                                         agent_name=agent.name(),
                                                         namespace=agent.name())
            logger.info(f"add_tool_result_to_memory finish {agent.id()}")
            return

        if hasattr(tool_result, 'content') and isinstance(tool_result.content, str) and tool_result.content.startswith(
                "data:image"):
            image_content = tool_result.content
            tool_result.content = "this picture is below "
            await self._do_add_tool_result_to_memory(agent, tool_call_id, tool_result, context)
            image_content = [
                {
                    "type": "text",
                    "text": f"this is file of tool_call_id:{tool_result.tool_call_id}"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_content
                    }
                }
            ]
            await self.add_human_input_to_memory(agent, image_content, context)
        else:
            await self._do_add_tool_result_to_memory(agent, tool_call_id, tool_result, context)

    async def _do_add_tool_result_to_memory(self, agent: 'Agent', tool_call_id: str, tool_result: ActionResult, context: Context):
        """Add tool result to memory"""
        memory = MemoryFactory.instance()
        tool_use_summary = None
        if isinstance(tool_result, ActionResult):
            tool_use_summary = tool_result.metadata.get("tool_use_summary")
        await memory.add(MemoryToolMessage(
            content=tool_result.content if hasattr(tool_result, 'content') else tool_result,
            tool_call_id=tool_call_id,
            status="success",
            metadata=MessageMetadata(
                session_id=context.get_task().session_id,
                user_id=context.get_task().user_id,
                task_id=context.get_task().id,
                agent_id=agent.id(),
                agent_name=agent.name(),
                summary_content=tool_use_summary
            )
        ), agent_memory_config=agent.memory_config)

    def _is_amni_context(self, context: Context):
        from aworld.core.context.amni import AmniContext
        return isinstance(context, AmniContext)


