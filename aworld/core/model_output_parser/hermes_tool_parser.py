# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import re
import uuid

from aworld.core.model_output_parser.base_content_parser import BaseContentParser
from aworld.core.model_output_parser.default_parsers import ToolParser
from aworld.logs.util import logger
from aworld.models.model_response import ModelResponse, ToolCall, Function



class HermesToolParser(ToolParser):
    """Adapted from https://github.com/vllm-project/vllm/blob/v0.9.1/vllm/entrypoints/openai/tool_parsers/hermes_tool_parser.py."""

    def __init__(self) -> None:
        self.tool_call_start_token: str = "<tool_call>"
        self.tool_call_end_token: str = "</tool_call>"
        self.tool_call_regex = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)

    async def extract_tool_calls(self, content: str) -> tuple[str, list[ToolCall]]:
        if self.tool_call_start_token not in content or self.tool_call_end_token not in content:
            return content, []

        matches = self.tool_call_regex.findall(content)
        function_calls = []
        for match in matches:
            try:
                function_call = json.loads(match)
                name, arguments = function_call["name"], function_call["arguments"]
                function_calls.append(Function(name=name, arguments=json.dumps(arguments, ensure_ascii=False)))
            except Exception as e:
                logger.error(f"Failed to decode tool call: {e}")

        # content exclude tool calls
        content = self.tool_call_regex.sub("", content)
        content = self.tool_call_regex.sub("", content)
        tool_calls = []
        if function_calls:
            tool_calls = [ToolCall(id=f"toolcall_{uuid.uuid4().hex}", function=tool_call) for tool_call in function_calls]
            logger.info(f"{len(tool_calls)} tool calls extracted: {tool_calls}")

        return content, tool_calls