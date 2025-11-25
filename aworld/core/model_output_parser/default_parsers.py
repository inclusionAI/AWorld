# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from typing import Any, Dict, List, Optional
from aworld.core.model_output_parser.base_content_parser import BaseContentParser
from aworld.models.model_response import ModelResponse


class ToolParser(BaseContentParser):
    """Default parser for tool calls."""
    
    @property
    def parser_type(self) -> str:
        return "tool"

    def parse(self, resp: ModelResponse, **kwargs) -> Any:
        """Parse tool calls from model response."""
        # Default implementation placeholder
        return resp


class ReasoningParser(BaseContentParser):
    """Default parser for reasoning/thinking process."""
    
    @property
    def parser_type(self) -> str:
        return "thinking"

    def parse(self, resp: ModelResponse, **kwargs) -> Any:
        """Parse reasoning content from model response."""
        # Default implementation placeholder
        return resp


class CodeParser(BaseContentParser):
    """Default parser for code blocks."""
    
    @property
    def parser_type(self) -> str:
        return "code"

    def parse(self, resp: ModelResponse, **kwargs) -> Any:
        """Parse code blocks from model response."""
        # Default implementation placeholder
        return resp


class JsonParser(BaseContentParser):
    """Default parser for JSON content."""
    
    @property
    def parser_type(self) -> str:
        return "json"

    def parse(self, resp: ModelResponse, **kwargs) -> Any:
        """Parse JSON content from model response."""
        # Default implementation placeholder
        return resp

