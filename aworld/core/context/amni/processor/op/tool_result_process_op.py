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
from aworldspace.utils.workspace_utils import extract_artifacts_from_toolresult

CONTEXT_OFFLOAD_TOOL_NAME_WHITE = ["aworldsearch-server:search", "readweb-server:read_url",
                                   "web-search-server:search_web", "google-search-server",
                                   "wiki-server:get_article_content", "wiki-server:get_article_summary",
                                   "wiki-server:get_article_history", "wiki-server:get_wikipedia_capabilities",
                                   "arxiv-server:load_article_to_context",
                                   "wiki-server:get_article_categories","wiki-server:get_article_links",
                                   "ms-playwright:browser_snapshot", "ms-playwright:browser_navigate",
                                   "ms-playwright:browser_click","ms-playwright:browser_type",
                                   "ms-playwright:browser_evaluate","ms-playwright:browser_tab_select",
                                   "ms-playwright:browser_press_key","ms-playwright:browser_wait_for"
                                   ]

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

        items = await self._resolve_tool_result(event.agent_id, event.tool_call_id, event.tool_result, context)
        for item in items:
            info["memory_commands"].append(MemoryCommand(type="ADD", item=item))
        return info

    async def _resolve_tool_result(self,
                                   namespace: str,
                                   tool_call_id: str,
                                   tool_result: ActionResult,
                                   context: ApplicationContext) -> Optional[list[MemoryMessage]]:
        try:
            ## 判断工具是否在上下文卸载白名单里面
            need_offload = await self._need_offload(tool_result)
            if need_offload:
                # 卸载工具结果
                tool_result_prompt = await self._offload_tool_result(namespace, tool_result, context)

                ## 更新工具结果
                tool_result.content = tool_result_prompt

                logger.info(f"Offload finished: {namespace} -> {tool_call_id}: {len(tool_result.content)}")

            return await MemoryItemConvertor.convert_tool_result_to_memory(namespace, tool_call_id, tool_result, context)

        except Exception as err:
            logger.warning(
                f"extract_artifacts_from_toolresult execute failed is {err}, trace is {traceback.format_exc()}")

    async def _need_offload(self, tool_result):
        # return False
        if isinstance(tool_result, ActionResult):
            if f"{tool_result.tool_name}:{tool_result.action_name}" in CONTEXT_OFFLOAD_TOOL_NAME_WHITE:
                logger.info(
                    f"{tool_result.tool_name}:{tool_result.action_name} in CONTEXT_OFFLOAD_TOOL_NAME_WHITE, need compress")
                return True
            if tool_result.metadata and tool_result.metadata.get("offload", False) == True:
                logger.info(f"tool_result.tool_name:tool_result.metadata.offload Enable, need compress")
                return True
            if num_tokens_from_string(tool_result.content) > 30_1000:
                logger.info(f"tool_result.tool_name:tool_result.content is too large, need compress")
                return True
            return False
        elif isinstance(tool_result, str):
            if num_tokens_from_string(tool_result) > 30_1000:
                logger.info(f"tool_result is too large, need compress")
                return True
        else:
            return False

    async def _offload_tool_result(self, namespace: str, tool_result: ActionResult, context: ApplicationContext):
        """
        offload tool result to workspace

        Args:
            namespace: agent namespace
            tool_result: tool result
            context: context

        Returns: offloaded tool result

        """

        ## 将本次检索到的内容抽取成artifacts
        artifacts = extract_artifacts_from_toolresult(tool_result)
        for index, artifact in enumerate(artifacts):
            logger.info(
                f"offload_tool_result#[{tool_result.tool_call_id}] -> artifact#{index} [{artifact.artifact_id}:{artifact.title}]")
            artifact.metadata.update({
                "agent_id": namespace,
                "task_id": context.task_id,
                "session_id": context.session_id
            })

        ## 卸载上下文
        offloaded_content = await context.offload_by_workspace(artifacts=artifacts, biz_id=tool_result.tool_call_id)
        #
        # tool_result_content_tokens = num_tokens_from_string(tool_result.content)
        # offloaded_content_tokens = num_tokens_from_string(offloaded_content)
        # logger.info(f"[OFFLOAD_CONTEXT]offload metrics is (origin[{tool_result_content_tokens}] -> result[{offloaded_content_tokens}]); offload rate is {offloaded_content_tokens / tool_result_content_tokens:.3f}")
        return offloaded_content
