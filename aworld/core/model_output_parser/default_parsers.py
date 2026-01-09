# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import re
import uuid
from typing import Any, Dict, List, Optional
from aworld.core.model_output_parser.base_content_parser import BaseContentParser
from aworld.logs.util import logger
from aworld.models.model_response import ModelResponse, ToolCall, Function


class ToolParser(BaseContentParser):
    """Default parser for tool calls: <tool_call>...</tool_call>."""

    def __init__(self) -> None:
        self.tool_call_start_token: str = "<tool_call>"
        self.tool_call_end_token: str = "</tool_call>"
        self.tool_call_regex = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
    
    @property
    def parser_type(self) -> str:
        return "tool"

    async def parse(self, resp: ModelResponse, **kwargs) -> Any:
        """Parse tool calls from model response."""
        if not kwargs.get("use_tools_in_prompt", False):
            return resp
        raw_content = resp.content
        content, tool_calls = await self.extract_tool_calls(raw_content, **kwargs)
        resp.content = content
        resp.tool_calls = tool_calls
        return resp

    async def extract_tool_calls(self, content: str, **kwargs) -> tuple[str, list[ToolCall]]:
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
        tool_calls = []
        if function_calls:
            tool_calls = [ToolCall(id=f"toolcall_{uuid.uuid4().hex}", function=tool_call) for tool_call in
                          function_calls]
            logger.info(f"{len(tool_calls)} tool calls extracted: {tool_calls}")

        return content, tool_calls


class ReasoningParser(BaseContentParser):
    """Default parser for reasoning/thinking process: <thinking>...</thinking>."""

    def __init__(self) -> None:
        self.thinking_start_token: str = "<think>"
        self.thinking_end_token: str = "</think>"
        self.thinking_regex = re.compile(r"<think>(.*?)</think>", re.DOTALL)

    @property
    def parser_type(self) -> str:
        return "thinking"

    async def parse(self, resp: ModelResponse, **kwargs) -> Any:
        """Parse reasoning content from model response."""
        content, thinking_content = await self.extract_thinking_content(resp.content)
        resp.reasoning_content = thinking_content
        return resp

    async def extract_thinking_content(self, content: str) -> tuple[str, str]:
        if self.thinking_start_token not in content or self.thinking_end_token not in content:
            return content, ""

        matches = self.thinking_regex.findall(content)
        thinking_content = ""
        if matches:
            # Concatenate all matched thinking content, separated if needed
            thinking_content = "\n".join(match.strip() for match in matches if match.strip())
            # Remove all <thinking>...</thinking> blocks from content
            content = self.thinking_regex.sub("", content)
        return content, thinking_content


class CodeParser(BaseContentParser):
    """Default parser for code blocks: ```python\n...\n```"""

    def __init__(self) -> None:
        self.code_block_regex = re.compile(r"```(python)\s*\n(.*?)```", re.DOTALL)
    
    @property
    def parser_type(self) -> str:
        return "code"

    async def parse(self, resp: ModelResponse, **kwargs) -> Any:
        """Parse code blocks from model response."""
        content, code_blocks = await self.extract_code_blocks(resp.content)
        resp.structured_output["code_blocks"] = code_blocks
        return resp

    async def extract_code_blocks(self, content: str) -> tuple[str, List[Dict[str, str]]]:
        if "```" not in content:
            return content, []
            
        matches = self.code_block_regex.findall(content)
        code_blocks = []
        for lang, code in matches:
            code_blocks.append({
                "language": lang.strip(),
                "code": code.strip()
            })
            
        if code_blocks:
            content = self.code_block_regex.sub("", content).strip()
            logger.info(f"{len(code_blocks)} code blocks extracted")
            
        return content, code_blocks


class JsonParser(BaseContentParser):
    """Default parser for JSON content."""

    def __init__(self) -> None:
        self.json_block_regex = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)
    
    @property
    def parser_type(self) -> str:
        return "json"

    async def parse(self, resp: ModelResponse, **kwargs) -> Any:
        """Parse JSON content from model response."""
        content, json_data = await self.extract_json_content(resp.content)
        resp.structured_output["parsed_json"] = json_data
        return resp

    async def extract_json_content(self, content: str) -> tuple[str, List[Any]]:
        if "```json" not in content:
            return content, []

        matches = self.json_block_regex.findall(content)
        json_objects = []
        for json_str in matches:
            try:
                json_obj = json.loads(json_str.strip())
                json_objects.append(json_obj)
            except Exception as e:
                logger.error(f"Failed to decode JSON block: {e}")
                
        if json_objects:
            content = self.json_block_regex.sub("", content).strip()
            logger.info(f"{len(json_objects)} JSON blocks extracted")
            
        return content, json_objects

