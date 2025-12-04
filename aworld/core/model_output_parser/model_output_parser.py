# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from typing import TypeVar, Generic, Any, Dict, List, Optional

from aworld.core.model_output_parser.base_content_parser import BaseContentParser
from aworld.core.model_output_parser.default_parsers import ToolParser, ReasoningParser, CodeParser, JsonParser
from aworld.logs.util import logger

INPUT = TypeVar('INPUT')
OUTPUT = TypeVar('OUTPUT')


class ModelOutputParser(Generic[INPUT, OUTPUT]):
    """
    ModelOutputParser is responsible for parsing the output from a model (LLM).
    It manages a collection of sub-parsers (BaseContentParser) that handle specific types of content 
    such as tool calls, reasoning traces, code blocks, or JSON data.

    Users can extend this class to implement custom parsing logic or register new parsers 
    to handle additional content types.
    """
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    async def parse(self, content: INPUT, **kwargs) -> OUTPUT:
        """Parse the input content into the desired output format.
        
        This method should coordinate the execution of registered sub-parsers 
        to extract information from the content.

        Args:
            content (INPUT): The input content to parse (typically ModelResponse).
            **kwargs: Additional arguments to pass to the parsers.

        Returns:
            OUTPUT: The parsed output.
        """
        pass
