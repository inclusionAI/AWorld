from typing import Any, Optional

from amnicontext import ApplicationContext
from aworld.core.common import ActionResult
from aworld.memory.models import MemoryHumanMessage, MessageMetadata, MemoryToolMessage, MemoryMessage


class MemoryItemConvertor:
    """
    Memory item convertor
    """

    @staticmethod
    async def convert_tool_result_to_memory(namespace:str, tool_call_id: str, tool_result: ActionResult, context: ApplicationContext) -> \
            Optional[list[MemoryMessage]]:
        """
        Convert tool result to memory
        Args:
            namespace: agent namespace
            tool_call_id: tool call id
            tool_result: tool result
            context: context

        Returns: tool result memory

        """
        if hasattr(tool_result, 'content') and isinstance(tool_result.content, str) and tool_result.content.startswith(
                "data:image"):
            image_content = tool_result.content
            tool_result.content = "this picture is below "
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
            tool_memory_item= MemoryItemConvertor._convert_tool_result_to_memory(namespace, tool_call_id, tool_result, context)
            human_memory_item = await MemoryItemConvertor.convert_to_memory_human_message(tool_call_id, image_content, context, "message")
            return [tool_memory_item, human_memory_item]
        else:
            if isinstance(tool_result, dict):
                tool_result.content = f"{tool_result.content}"
            return [await MemoryItemConvertor._convert_tool_result_to_memory(namespace, tool_call_id, tool_result, context)]



    @staticmethod
    async def _convert_tool_result_to_memory(namespace: str, tool_call_id: str, tool_result: ActionResult,
                                            context: ApplicationContext):
        """Add tool result to memory"""
        tool_use_summary = None
        if isinstance(tool_result, ActionResult):
            tool_use_summary = tool_result.metadata.get("tool_use_summary")
        return MemoryToolMessage(
            content=tool_result.content if hasattr(tool_result, 'content') else tool_result,
            tool_call_id=tool_call_id,
            status="success",
            metadata=MessageMetadata(
                session_id=context.session_id,
                user_id=context.user_id,
                task_id=context.task_id,
                agent_id=namespace,
                agent_name=namespace,
                summary_content=tool_use_summary
            )
        )

    @staticmethod
    async def convert_to_memory_human_message(
            namespace: str,
            content: Any,
            context: ApplicationContext,
            memory_type="init") -> MemoryHumanMessage:
        """
        Convert to memory human message
        Args:
            namespace:
            content:
            context:
            memory_type:

        Returns:

        """
        return MemoryHumanMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=context.session_id,
                user_id=context.user_id,
                task_id=context.task_id,
                agent_id=namespace,
                agent_name=namespace,
            ),
            memory_type=memory_type
        )
