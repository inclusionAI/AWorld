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
from ...prompt.prompt_ext import ContextPromptTemplate

# TODO add to config - Define event whitelist
EVENT_NAME_WHITE = ["ms-playwright", "wiki-server"]


class LlmExtractOp(BaseOp):


    async def execute(self, context: ApplicationContext, info: Dict[str, Any] = None, event: ToolResultEvent = None, **kwargs) -> Dict[str, Any]:
        """
        Execute knowledge graph memory extraction operation

        Args:
            context: Application context
            info: Operation info dictionary
            event: Tool result event
            **kwargs: Additional parameters

        Returns:
            Updated info dictionary containing memory commands
        """
        try:
            if not event or not event.tool_result or not isinstance(event.tool_result, ActionResult):
                logger.warning("No event provided for graph memory extraction")
                return {}

            if f"{event.tool_result.tool_name}" not in EVENT_NAME_WHITE:
                return {}

            # Prepare extraction text
            extraction_text = await self._prepare_extraction_text(context, info, event.namespace, event)
            if not extraction_text:
                logger.warning("No extraction text available")
                return {}

            # Call LLM for extraction
            extraction_result = await self._call_llm_for_extraction(context=context, text=extraction_text)
            if not extraction_result:
                logger.warning("no valid LLM extraction")
                return {}

            # Convert to memory commands
            memory_commands = self._convert_extraction_to_memory_commands(extraction_result, context, event.namespace)

            # Update info dictionary
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
        Call LLM for knowledge graph extraction

        Args:
            text: Text to extract from

        Returns:
            Extraction result dictionary
        """
        try:
            # Build prompt
            prompt = self._build_extraction_prompt_template()
            full_prompt = await ContextPromptTemplate(template=prompt).async_format(context=context, text=text)

            # Build request data
            messages = [
                {
                    "role": "system",
                    "content": "You are a professional long-term memory analyst who can accurately extract factual memories from conversations."
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
            # Remove <think>...</think> parts
            result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL)
            result = re.sub(r'```json', '', result, flags=re.DOTALL)
            result = re.sub(r'```', '', result, flags=re.DOTALL)
            return json.loads(result)
        except Exception as e:
            logger.error(f"❌ Error calling LLM for extraction: {e} {traceback.print_exc()}")
            return None


