import traceback
from typing import Dict, Any, Optional

from aworld.logs.util import logger
from ... import ApplicationContext
from ...event import ToolResultEvent
from .op_factory import memory_op
from .base import MemoryCommand, BaseOp
from ...utils.memoryutils import MemoryItemConvertor
from ...utils.modelutils import num_tokens_from_string
from aworld.core.common import ActionResult
from aworld.memory.models import MemoryMessage
from ...utils.workspace_utils import extract_artifacts_from_toolresult


@memory_op("tool_result_offload")
class ToolResultOffloadOp(BaseOp):
    """ToolResultProcess"""

    async def execute(self, context: ApplicationContext, info: Dict[str, Any] = None, event: ToolResultEvent = None,
                      **kwargs) -> Dict[str, Any]:
        if not event:
            logger.warning("ToolResultProcessOp execute failed is event is None")
            return {}
        if not info.get("memory_commands"):
            info["memory_commands"] = []

        items = await self._resolve_tool_result(event.agent_id, event.tool_call_id, event.tool_result, context, event)
        for item in items:
            info["memory_commands"].append(MemoryCommand(type="ADD", item=item))
        return info

    async def _resolve_tool_result(self,
                                   namespace: str,
                                   tool_call_id: str,
                                   tool_result: ActionResult,
                                   context: ApplicationContext,
                                   event: ToolResultEvent
                                   ) -> Optional[list[MemoryMessage]]:
        try:
            # Check if tool is in context offload whitelist
            need_offload = await self._need_offload(tool_result, context, event)
            if need_offload:
                # Offload tool result
                tool_result_prompt = await self._offload_tool_result(namespace, tool_result, context)

                # Update tool result
                tool_result.content = tool_result_prompt

                logger.info(f"Offload finished: {namespace} -> {tool_call_id}: {len(tool_result.content)}")

            return await MemoryItemConvertor.convert_tool_result_to_memory(namespace, tool_call_id, tool_result, context)

        except Exception as err:
            logger.warning(
                f"extract_artifacts_from_toolresult execute failed is {err}, trace is {traceback.format_exc()}")

    async def _need_offload(self, tool_result, context: ApplicationContext, event: ToolResultEvent) -> Optional[bool]:
        agent_context_config = context.get_config().get_agent_context_config(event.agent_id)
        if not agent_context_config.tool_result_offload:
            return False
        if isinstance(tool_result, ActionResult):
            if agent_context_config.tool_action_white_list and isinstance(agent_context_config.tool_action_white_list, list) and f"{tool_result.tool_name}:{tool_result.action_name}" in agent_context_config.tool_action_white_list:
                logger.info(
                    f"📦 {tool_result.tool_name}:{tool_result.action_name} in CONTEXT_OFFLOAD_TOOL_NAME_WHITE, need compress")
                return True
            if tool_result.metadata and tool_result.metadata.get("offload", False) == True:
                logger.info(f"📦 tool_result.tool_name:tool_result.metadata.offload Enable, need compress")
                return True
            if num_tokens_from_string(tool_result.content) > agent_context_config.tool_result_length_threshold:
                logger.info(f"📦 tool_result.tool_name:tool_result.content is too large, need compress")
                return True
            return False
        elif isinstance(tool_result, str):
            if num_tokens_from_string(tool_result) > agent_context_config.tool_result_length_threshold:
                logger.info(f"📦 tool_result is too large, need compress")
                return True
        else:
            return False

    async def _offload_tool_result(self, namespace: str, tool_result: ActionResult, context: ApplicationContext):
        """
        Offload tool result to workspace

        Args:
            namespace: agent namespace
            tool_result: tool result
            context: context

        Returns: offloaded tool result

        """

        # Extract artifacts from tool result
        artifacts = extract_artifacts_from_toolresult(tool_result)
        for index, artifact in enumerate(artifacts):
            logger.info(
                f"📤 offload_tool_result#[{tool_result.tool_call_id}] -> artifact#{index} [{artifact.artifact_id}:{artifact.title}]")
            artifact.metadata.update({
                "agent_id": namespace,
                "task_id": context.task_id,
                "session_id": context.session_id
            })

        # Do offload
        offloaded_content = await context.offload_by_workspace(artifacts=artifacts, biz_id=tool_result.tool_call_id)
        return offloaded_content
