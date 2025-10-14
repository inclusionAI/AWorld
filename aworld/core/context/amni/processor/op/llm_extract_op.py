import json
import os
import re
import traceback
from abc import abstractmethod
from typing import Any, Dict, Optional, List

from openai import OpenAI

from ... import ApplicationContext
from ...event import ToolResultEvent
from .base import BaseOp, MemoryCommand
from aworld.core.common import ActionResult
from aworld.memory.models import Fact, MemoryItem
from aworldspace.prompt.prompt_ext import ContextPromptTemplate

# TODO add to config 定义事件白名单
EVENT_NAME_WHITE = ["ms-playwright", "wiki-server"]


class LlmExtractOp(BaseOp):


    async def execute(self, context: ApplicationContext, info: Dict[str, Any] = None, event: ToolResultEvent = None, **kwargs) -> Dict[str, Any]:
        """
        执行知识图谱记忆提取操作

        Args:
            context: 应用上下文
            info: 操作信息字典
            event: 工具结果事件
            **kwargs: 额外参数

        Returns:
            更新后的信息字典，包含内存命令
        """
        try:
            if not event or not event.tool_result or not isinstance(event.tool_result, ActionResult):
                logger.warning("No event provided for graph memory extraction")
                return {}

            if f"{event.tool_result.tool_name}" not in EVENT_NAME_WHITE:
                return {}

            # 准备提取文本
            extraction_text = await self._prepare_extraction_text(context, info, event.namespace, event)
            if not extraction_text:
                logger.warning("No extraction text available")
                return {}

            # 调用 LLM 进行提取
            extraction_result = await self._call_llm_for_extraction(context=context, text=extraction_text)
            if not extraction_result:
                logger.warning("no valid LLM extraction")
                return {}

            # 转换为内存命令
            memory_commands = self._convert_extraction_to_memory_commands(extraction_result, context, event.namespace)

            # 更新 info 字典
            if not info:
                info = {}
            if info.get("memory_commands") is None:
                info["memory_commands"] = []
            info["memory_commands"].extend(memory_commands)

            logger.info(f"✅ Successfully extracted {len(memory_commands)} memory items")
            return info

        except Exception as e:
            logger.error(f"❌ Error during memory extraction: {e} {traceback.format_exc()}")
            return {}

    @abstractmethod
    async def _prepare_extraction_text(self, context: ApplicationContext, info: Dict[str, Any] = None, agent_id: str = None, event: ToolResultEvent = None) -> str:
        pass

    @abstractmethod
    def _build_extraction_prompt_template(self) -> str:
        pass

    @abstractmethod
    def _convert_extraction_to_memory_commands(self, extraction_result: Dict[str, Any], context: ApplicationContext, agent_id: str) -> List[MemoryCommand[MemoryItem]]:
        pass

    async def _call_llm_for_extraction(self, context: ApplicationContext, text: str) -> Optional[Dict[str, Any]]:
        """
        调用 LLM 进行知识图谱提取

        Args:
            text: 要提取的文本

        Returns:
            提取结果字典
        """
        try:
            # 构建提示
            prompt = self._build_extraction_prompt_template()
            full_prompt = await ContextPromptTemplate(template=prompt).async_format(context=context, text=text)

            # 构建请求数据
            messages = [
                {
                    "role": "system",
                    "content": "你是一个专业的长期记忆分析师，能够从对话中准确提取长期记忆的事实记忆。"
                },
                {
                    "role": "user",
                    "content": full_prompt
                }
            ]


            client = OpenAI(api_key=os.environ['EXTRACT_API_KEY'], base_url=os.environ['EXTRACT_BASE_URL'])
            response = client.chat.completions.create(
                model=os.environ['EXTRACT_MODEL_NAME'],
                messages=messages,
                temperature=0.5,
                max_tokens=4000,
                response_format={"type": "json_object"},
                stream=False
            )
            result = response.choices[0].message.content or '{}'
            # 去掉 <think>...</think> 部分
            result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL)
            result = re.sub(r'```json', '', result, flags=re.DOTALL)
            result = re.sub(r'```', '', result, flags=re.DOTALL)
            return json.loads(result)
        except Exception as e:
            logger.error(f"❌ Error calling LLM for extraction: {e} {traceback.print_exc()}")
            return None


