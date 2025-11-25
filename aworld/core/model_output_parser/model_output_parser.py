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

    def __init__(self, parsers: List[BaseContentParser] = None) -> None:
        """Initialize the ModelOutputParser with default parsers and optional user-defined parsers.

        Args:
            parsers (List[BaseContentParser], optional): A list of custom parsers to register.
                These parsers will override default parsers if they share the same parser_type.
        """
        self._parsers: Dict[str, BaseContentParser] = {}
        
        # Initialize default parsers
        default_parsers = [
            ToolParser(),
            ReasoningParser(),
            CodeParser(),
            JsonParser()
        ]
        
        for parser in default_parsers:
            self.register_parser(parser)

        # Register user provided parsers
        if parsers:
            for parser in parsers:
                self.register_parser(parser)

    def register_parser(self, parser: BaseContentParser) -> None:
        """Register a new content parser.
        
        If a parser with the same type already exists, it will be overwritten.

        Args:
            parser (BaseContentParser): The parser instance to register.
        """
        self._parsers[parser.parser_type] = parser

    def get_parser(self, parser_type: str) -> Optional[BaseContentParser]:
        """Retrieve a registered parser by its type.

        Args:
            parser_type (str): The type of the parser to retrieve (e.g., 'tool', 'thinking').

        Returns:
            Optional[BaseContentParser]: The parser instance if found, otherwise None.
        """
        return self._parsers.get(parser_type)

    def get_parsers(self) -> Dict[str, BaseContentParser]:
        """Get all registered parsers.

        Returns:
            Dict[str, BaseContentParser]: A dictionary mapping parser types to parser instances.
        """
        return self._parsers

    def list_supported_parser_types(self) -> List[str]:
        """List all supported parser types currently registered.

        Returns:
            List[str]: A list of parser type strings (e.g., ['tool', 'thinking', 'code', 'json']).
        """
        return list(self._parsers.keys())

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
