# aworld/runners/handler/output.py
import copy
import json
import time
import traceback
from datetime import datetime
from typing import AsyncGenerator, Any

from aworld.agents.llm_agent import Agent
from aworld.config import ConfigDict
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

                # Update the usage of the last message before adding a new AI message
                if llm_response and hasattr(llm_response, 'usage') and llm_response.usage:
                    await self._update_last_message_usage(agent, llm_response, context)

                # Get skip_summary parameter from headers
                skip_summary = message.headers.get("skip_summary", False)
                await self._add_llm_response_to_memory(agent, llm_response, context, history_messages, skip_summary=skip_summary)

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
            logger.warning(f"DefaultMemoryHandler: failed to write memory for event. {traceback.format_exc()}")

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

        memory = MemoryFactory.instance()
        histories = memory.get_last_n(0, filters={
            "agent_id": agent.id(),
            "session_id": session_id,
            "task_id": task_id
        }, agent_memory_config=agent.memory_config)
        if histories:
            logger.debug(f"🧠 [MEMORY:short-term] histories is not empty, do not need add system input to agent memory")
            return

        system_message = MemorySystemMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=agent.id(),
                agent_name=agent.name(),
            )
        )
        # Record message end time
        system_message.end_time = None
        await memory.add(system_message, agent_memory_config=agent.memory_config)

    async def _update_last_message_usage(self, agent: Agent, llm_response, context: Context):
        """Update the usage information of the last message"""
        agent_memory_config = agent.memory_config
        if self._is_amni_context(context):
            agent_memory_config = context.get_config().get_agent_context_config(agent.id())

        filters = {
            "agent_id": agent.id(),
            "session_id": context.get_task().session_id,
            "task_id": context.get_task().id,
        }
        memory = MemoryFactory.instance()
        # Get the last message
        last_messages = memory.get_last_n(1, filters=filters, agent_memory_config=agent_memory_config)
        if last_messages and len(last_messages) > 0:
            last_message = last_messages[-1]
            # Update usage in metadata
            last_message.metadata['usage'] = llm_response.usage
            last_message.updated_at = datetime.now().isoformat()
            # Update memory
            memory.update(last_message)

    async def _add_llm_response_to_memory(self, agent: Agent, llm_response, context: Context, history_messages: list, skip_summary: bool = False, **kwargs):
        """Add LLM response to memory"""
        # Get start time from context (if exists)
        start_time = context.context_info.get("llm_call_start_time")
        
        ai_message = MemoryAIMessage(
            content=llm_response.content,
            tool_calls=llm_response.tool_calls,
            reasoning_details=llm_response.reasoning_details,
            metadata=MessageMetadata(
                session_id=context.get_task().session_id,
                user_id=context.get_task().user_id,
                task_id=context.get_task().id,
                agent_id=agent.id(),
                agent_name=agent.name(),
                ext_info={
                    "tools": agent.tools
                }
            )
        )
        
        # If start time exists in context, update it
        if start_time:
            ai_message.start_time = start_time
        # Record message end time
        ai_message.end_time = None
        
        agent_memory_config = agent.memory_config
        if self._is_amni_context(context):
            agent_memory_config = context.get_config().get_agent_memory_config(agent.id())

        # If skip_summary is True, disable summary
        if skip_summary and agent_memory_config:
            if not isinstance(agent_memory_config, ConfigDict):
               agent_memory_config = copy.deepcopy(agent_memory_config)
            else:
               agent_memory_config = copy.copy(agent_memory_config)
            agent_memory_config.enable_summary = False
        await MemoryFactory.instance().add(ai_message, agent_memory_config=agent_memory_config)

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

        human_message = MemoryHumanMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=agent.id(),
                agent_name=agent.name(),
            ),
            memory_type=memory_type
        )
        # Record message end time
        human_message.end_time = None
        await MemoryFactory.instance().add(human_message, agent_memory_config=agent_memory_config)

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
        
        # Get start time from context (if exists)
        start_time = context.context_info.get(f"tool_call_start_time_{tool_call_id}")
        
        tool_message = MemoryToolMessage(
            content=tool_result.content if hasattr(tool_result, 'content') else tool_result,
            tool_call_id=tool_call_id,
            status="success",
            metadata=MessageMetadata(
                session_id=context.get_task().session_id,
                user_id=context.get_task().user_id,
                task_id=context.get_task().id,
                agent_id=agent.id(),
                agent_name=agent.name(),
                summary_content=tool_use_summary,
                ext_info={"tool_name": tool_result.tool_name, "action_name": tool_result.action_name}
            )
        )
        
        # If start time exists in context, update it
        if start_time:
            tool_message.start_time = start_time
        
        # Record message end time
        tool_message.end_time = None
        
        await memory.add(tool_message, agent_memory_config=agent.memory_config)

    def _is_amni_context(self, context: Context):
        from aworld.core.context.amni import AmniContext
        return isinstance(context, AmniContext)

    @staticmethod
    async def handle_memory_message_directly(memory_msg: MemoryEventMessage, context: Context):
        """Handle memory message directly without going through message system
        
        Args:
            memory_msg: Memory event message
            context: Context object
        """
        try:
            # Create a simple runner object, only needs task attribute
            class SimpleRunner:
                def __init__(self, task):
                    self.task = task
                    self.start_time = 0
            
            task = context.get_task()
            simple_runner = SimpleRunner(task)
            handler = DefaultMemoryHandler(simple_runner)
            start_time = time.time()
            # Directly call _do_handle method
            async for _ in handler._do_handle(memory_msg):
                pass  # _do_handle is an async generator, needs to be consumed
            logger.info(f"Direct memory call completed in {1000*(time.time() - start_time):.2f}ms {memory_msg}")
        except Exception as e:
            logger.warn(f"Direct memory call failed: {traceback.format_exc()}")
