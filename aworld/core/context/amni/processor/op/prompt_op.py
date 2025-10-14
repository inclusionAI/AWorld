from typing import Any, Dict

from ... import ApplicationContext
from aworld.logs.util import logger
from ...config import NeuronStrategyConfig
from ...event import ToolResultEvent,SystemPromptEvent
from .base import BaseOp
from .op_factory import memory_op
from ...prompt.neurons import neuron_factory
from aworld.memory.models import MemorySystemMessage, MessageMetadata, MemoryHumanMessage
from aworldspace.prompt.prompt_ext import ContextPromptTemplate


@memory_op("append_user_prompt")
class AppendUserPromptOp(BaseOp):
    """
    更新用户提示词算子
    处理用户提示词的内存操作，包括添加和更新
    """

    def __init__(self, name: str = "append_user_prompt", **kwargs):
        super().__init__(name, **kwargs)

    def filter_prompts(self, augment_prompts: dict, target_strategy: str, namespace: str) -> dict:
        result: dict = {}
        for key, value in augment_prompts.items():
            # 获取neuron对应的strategy
            strategy = neuron_factory.get_neuron_strategy(name=key, namespace=namespace)
            if isinstance(strategy, NeuronStrategyConfig) and strategy.prompt_augment_strategy == target_strategy:
                result[key] = value
        return result

    async def execute(self, context: ApplicationContext, **kwargs) -> Dict[str, Any]:
        info: dict = kwargs.get("info", None)
        event: ToolResultEvent = kwargs.get("event", None)

        # agent信息
        agent_id = event.agent_id
        augment_prompts = info.get("augment_prompts", {})
        memory = event.memory
        namespace = event.namespace

        if not augment_prompts:
            logger.debug("No formatted content provided for system prompt update")
            return {"success": False, "message": "No content to update"}

        # filter append
        augment_prompts = self.filter_prompts(augment_prompts=augment_prompts, target_strategy="append", namespace=namespace)

        if not augment_prompts:
            logger.debug("No formatted content provided for system prompt update")
            return {"success": False, "message": "No content to update"}

        # join
        augment_prompt = "额外检索信息: " + "\n".join(augment_prompts.values())

        if not memory:
            logger.warning("No memory instance provided for system prompt update")
            return {"success": False, "message": "No memory instance"}

        # agent配置
        agent_state = context.get_agent_state(event.agent_id)
        if not agent_state:
            logger.warning(f"Agent state not found for agent_id: {event.agent_id}")
            return {"success": False, "message": f"Agent state not found for agent_id: {event.agent_id}"}

        # agent memory配置
        memory_config = agent_state.memory_config

        # 如果有历史记录，检查是否需要append
        await self._append_message(
            context=context, content=augment_prompt,
            memory=memory, memory_config=memory_config, agent_id=agent_id
        )
        logger.debug("System prompt updated in existing history")
        return {"success": True, "action": "updated", "message": "System prompt updated"}

    async def _append_message(self, context: ApplicationContext,
                                   content: str, memory, memory_config, agent_id: str):
        systems = memory.get_all(filters={
            "agent_id": agent_id,
            "task_id": context.task_id,
            "memory_type": "init"
        })
        if any(s.content == content and s.role == 'system' for s in systems):
            logger.info(f"skip append system message")
            return

        session_id = context.get_task().session_id
        task_id = context.get_task().id
        user_id = context.get_task().user_id
        # 查找第一条系统消息并更新
        append_message = MemorySystemMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=agent_id,
                agent_name="",
            ),
        )
        await memory.add(append_message, agent_memory_config=memory_config)
        logger.debug(f"append user message with id: {append_message.id}")


@memory_op("save_system_prompt")
class SaveSystemPromptOp(BaseOp):
    """
    更新系统提示词算子
    处理系统提示词的内存操作，包括添加和更新
    """

    def __init__(self, name: str = "save_system_prompt", **kwargs):
        super().__init__(name, **kwargs)

    async def execute(self, context: ApplicationContext, **kwargs) -> Dict[str, Any]:
        info: dict = kwargs.get("info", None)
        event: SystemPromptEvent = kwargs.get("event", None)

        # 获取任务信息
        session_id = context.get_task().session_id
        task_id = context.get_task().id
        agent_id = event.agent_id
        memory = event.memory
        user_query = event.user_query

        # prompt信息
        prompt = event.system_prompt
        augment_prompts = info.get("augment_prompts", {})
        appended_prompt = prompt + "\n\n" + "\n".join(augment_prompts.values())

        # 格式化
        formatted_system_prompt = await ContextPromptTemplate(template=appended_prompt).async_format(context=context,
                                                                                                     task=user_query)

        if not memory:
            logger.warning("No memory instance provided for system prompt update")
            return {"success": False, "message": "No memory instance"}

        # agent配置
        agent_state = context.get_agent_state(event.agent_id)
        if not agent_state:
            logger.warning(f"Agent state not found for agent_id: {event.agent_id}")
            return {"success": False, "message": f"Agent state not found for agent_id: {event.agent_id}"}

        # agent memory配置
        memory_config = agent_state.memory_config

        # 检查是否已有历史记录
        histories = memory.get_last_n(0, filters={
            "agent_id": event.agent_id,
            "session_id": session_id,
            "task_id": task_id
        }, agent_memory_config=memory_config)

        if histories:
            # 如果有历史记录，忽略
            logger.debug("System prompt updated in existing history")
            return {"success": True, "action": "updated", "message": "System prompt updated"}
        else:
            # 如果没有历史记录，添加新的系统消息
            await self._add_system_message(
                context=context, content=formatted_system_prompt, memory=memory,
                memory_config=memory_config, agent_id=agent_id
            )
            logger.debug("New system prompt added to memory")
            return {"success": True, "action": "added", "message": "System prompt added"}

    async def _add_system_message(self, context: ApplicationContext, content: str,
                                  memory, memory_config, agent_id: str, agent_name: str = None):
        session_id = context.get_task().session_id
        task_id = context.get_task().id
        user_id = context.get_task().user_id

        system_message = MemorySystemMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=agent_id,
                agent_name=agent_name or 'unknown',
            )
        )

        await memory.add(system_message, agent_memory_config=memory_config)
        logger.debug(f"Added new system message for agent: {agent_id}")
